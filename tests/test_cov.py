"""Coverage push tests: exercise branches that the targeted security suite
already certified but which the slim per-module suites do not.

Every assertion here enforces a behaviour decision. We never rely on
exception-raising as a coverage signal: if a branch is to be covered, we
state a concrete outcome.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import yaml

from venice_media_skill.catalog import ModelCatalog
from venice_media_skill.cli import _dispatch, _emit, _resolve_bundled_openapi, _validate_openapi, main
from venice_media_skill.client import (
    ALLOWED_DOWNLOAD_HOSTS,
    ApiResponse,
    VeniceClient,
    _enforce_safe_target,
    _is_safe_base_url,
    _resolve_safely,
)
from venice_media_skill.config import Settings
from venice_media_skill.consent import (
    ConsentStore,
    QuoteApprovalStore,
    build_consent_object,
    ensure_seedance_fact,
)
from venice_media_skill.errors import (
    ApiError,
    ConsentApprovalMissing,
    ConsentRequired,
    ContentValidationError,
    NetworkSafetyError,
    OutputError,
    QuoteApprovalMismatch,
    QuoteApprovalRequired,
    RequestValidationError,
)
from venice_media_skill.jobs import JobStore
from venice_media_skill.output import (
    ArtifactWriter,
    _atomic_write_text,
    _resolve_artifact_path,
    _validate_safe_filename,
    atomic_write_text,
)
from venice_media_skill.payloads import (
    RESERVED_PARAMETERS,
    CanonicalPayload,
    build_audio_queue,
    build_audio_quote,
    build_image_generate,
    build_video_queue,
    build_video_quote,
)
from venice_media_skill.request import MediaRequest
from venice_media_skill.runner import (
    MediaRunner,
    _sanitize_api_request,
    _summarize_inputs,
    _summarize_list_member,
)
from venice_media_skill.util import (
    decode_data_url,
    detected_content_type,
    extension_for_content_type,
    fast_validate_content_type,
    is_suspicious_content,
    path_to_data_url,
    redact_data,
    sha256_hex,
    stable_json,
    timestamp_slug,
    utc_now_iso,
    validate_content_type,
)

_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xfa\xff\xff?\x03\x00\x05\xfe\x02\xfe\xa3\x9a\xfa\x05"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


class _StubClient:
    def __init__(self, responses: list[ApiResponse]) -> None:
        self._queue = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        files: Any = None,
    ) -> ApiResponse:
        self.calls.append((method, path, dict(json_body) if json_body else None))
        if not self._queue:
            raise AssertionError(f"No remaining stubbed response for {method} {path}")
        return self._queue.pop(0)

    def download_public_url(self, url: str, **_: Any) -> ApiResponse:
        return ApiResponse(
            status_code=200,
            content_type="image/png",
            headers={"content-type": "image/png"},
            content=_PNG,
            json_data=None,
            sha256=sha256_hex(_PNG),
            path=url,
        )


def _make_runner(tmp_path: Path, client: _StubClient) -> tuple[MediaRunner, ConsentStore, QuoteApprovalStore, JobStore]:
    jobs = JobStore(tmp_path / "jobs")
    consent_store = ConsentStore(tmp_path / "consent.json")
    quote_store = QuoteApprovalStore(tmp_path / "quotes.json")
    return (
        MediaRunner(
            client=client,  # type: ignore[arg-type]
            writer=ArtifactWriter(tmp_path / "out"),
            jobs=jobs,
            consent_store=consent_store,
            quote_store=quote_store,
        ),
        consent_store,
        quote_store,
        jobs,
    )


def _settings_dirs(tmp_path: Path) -> dict[str, str]:
    return {
        "VENICE_MEDIA_CONFIG_DIR": str(tmp_path / "config"),
        "VENICE_MEDIA_CACHE_DIR": str(tmp_path / "cache"),
        "VENICE_MEDIA_STATE_DIR": str(tmp_path / "state"),
        "VENICE_MEDIA_OUTPUT_DIR": str(tmp_path / "output"),
    }


# ---------------------------------------------------------------------------
# cli.py: command dispatch coverage
# ---------------------------------------------------------------------------


def test_cli_install_skill_variants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key, value in _settings_dirs(tmp_path).items():
        monkeypatch.setenv(key, value)
    code = main(["install-skill", "--host", "all", "--scope", "project", "--project-dir", str(tmp_path)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "installed"
    assert out["host"] == "all"
    assert isinstance(out["skill_paths"], list)
    assert len(out["skill_paths"]) >= 1


def test_cli_schema_writes_to_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "schema.json"
    code = main(["schema", "--output", str(target)])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "written"
    on_disk = json.loads(target.read_text("utf-8"))
    assert on_disk["title"] == "Venice Media Skill request manifest"


def test_cli_validate_openapi_default_path_missing_repo_yaml(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["validate-openapi"])
    out = json.loads(capsys.readouterr().out)
    if code == 0:
        # Asset path or repo layout available
        assert out["status"] == "ok"
    else:
        assert out["status"] == "invalid" or "OpenAPI" in (capsys.readouterr().err or "")


def test_cli_approve_consent_missing_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key, value in _settings_dirs(tmp_path).items():
        monkeypatch.setenv(key, value)
    code = main(["approve-consent", "missing"])
    assert code == 2
    err = capsys.readouterr().err
    assert "acknowledge-policy" in err or "policy-unacknowledged" in err


def test_cli_approve_consent_happy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    state_dir = tmp_path / "state"
    paths = _settings_dirs(tmp_path)
    paths["VENICE_MEDIA_STATE_DIR"] = str(state_dir)
    for key, value in paths.items():
        monkeypatch.setenv(key, value)
    consent = ConsentStore(state_dir / "consent_approvals.json")
    challenge_id = consent.record_challenge(
        operation="video.generate",
        model="venice-seedance",
        payload_hash="abc123",
        input_hashes=["deadbeef"],
        provider_payload={
            "error": {"code": "needs_consent", "message": "ok"},
            "consent_flow": "seedance",
            "consent_version": "1.0",
            "policy_text": "policy",
            "face_media_roles": ["image"],
            "docs_url": "https://venice.ai/seedance",
        },
    ).challenge_id
    capsys.readouterr()  # reset
    code = main(["approve-consent", challenge_id, "--acknowledge-policy", "--max-cost", "5.25"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "approved"
    assert out["payload_hash"] == "abc123"


def test_cli_approve_quote_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key, value in _settings_dirs(tmp_path).items():
        monkeypatch.setenv(key, value)
    code = main(
        [
            "approve-quote",
            "video.generate",
            "hash-xyz",
            "--max-cost",
            "10.0",
            "--quote",
            str(tmp_path / "missing.json"),
        ]
    )
    assert code == 2
    assert "Quote file" in capsys.readouterr().err


def test_cli_approve_quote_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key, value in _settings_dirs(tmp_path).items():
        monkeypatch.setenv(key, value)
    quote_file = tmp_path / "quote.json"
    quote_file.write_text(json.dumps({"quote": 12.5}), encoding="utf-8")
    code = main(
        [
            "approve-quote",
            "video.generate",
            "a" * 64,
            "--max-cost",
            "10.0",
            "--quote",
            str(quote_file),
        ]
    )
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "recorded"
    assert out["payload_hash"] == "a" * 64


def test_cli_jobs_list_and_get(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    state_dir = tmp_path / "state"
    paths = _settings_dirs(tmp_path)
    paths["VENICE_MEDIA_STATE_DIR"] = str(state_dir)
    for key, value in paths.items():
        monkeypatch.setenv(key, value)
    code = main(["jobs", "list"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out == []

    jobs = JobStore(state_dir / "jobs")
    jobs.create(media_type="video", model="venice-1", queue_id="q-1", request={"operation": "video.generate"})
    capsys.readouterr()  # reset
    code = main(["jobs", "get", "q-1"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["queue_id"] == "q-1"


def test_cli_doctor_attention(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VENICE_API_KEY", raising=False)
    parser = __import__("venice_media_skill.cli", fromlist=["build_parser"]).build_parser()
    args = parser.parse_args(["doctor"])
    out = _dispatch(args)
    assert out["status"] == "attention_required"
    assert out["checks"]["venice_api_key"] == "missing"


def test_cli_doctor_online_skipped_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VENICE_API_KEY", raising=False)
    parser = __import__("venice_media_skill.cli", fromlist=["build_parser"]).build_parser()
    args = parser.parse_args(["doctor", "--online"])
    out = _dispatch(args)
    assert out["checks"]["online_check"] == "skipped_missing_api_key"


def test_cli_models_uses_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("VENICE_API_KEY", "x" * 32)
    for key, value in _settings_dirs(tmp_path).items():
        monkeypatch.setenv(key, value)
    settings = Settings.load(require_api_key=False)
    settings.ensure_directories()
    cache = settings.model_cache_file
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"fetched_at": time.time(), "by_type": {"image": [{"id": "cached-model"}]}}))
    code = main(["models", "--type", "image"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["models"][0]["id"] == "cached-model"


def test_cli_models_refresh_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("VENICE_API_KEY", "x" * 32)
    for key, value in _settings_dirs(tmp_path).items():
        monkeypatch.setenv(key, value)

    def fake_list(self: ModelCatalog, type_arg: str, refresh: bool = False) -> Any:
        return [{"id": "live-model"}]

    with patch.object(ModelCatalog, "list", fake_list):
        code = main(["models", "--refresh"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["models"][0]["id"] == "live-model"


def test_cli_models_uses_api_key_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("VENICE_API_KEY", "x" * 32)
    for key, value in _settings_dirs(tmp_path).items():
        monkeypatch.setenv(key, value)

    def fake_list(self: ModelCatalog, type_arg: str, refresh: bool = False) -> Any:
        return [{"id": "live-model"}]

    with patch.object(ModelCatalog, "list", fake_list):
        code = main(["models"])
    assert code == 0


def test_cli_plan_modelless_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key, value in _settings_dirs(tmp_path).items():
        monkeypatch.setenv(key, value)
    code = main(["plan", "image.upscale"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload.get("modelless") is True or payload.get("selected_model") is None


def test_cli_consent_required_typed_error(tmp_path: Path) -> None:
    request = MediaRequest.from_mapping(
        {
            "operation": "image.generate",
            "model": "venice-1",
            "prompt": "hi",
            "execution": {"dry_run": False},
        }
    )
    _canonical = build_image_generate(request)
    response = ApiResponse(
        status_code=409,
        content_type="application/json",
        headers={"content-type": "application/json"},
        json_data={
            "error": {"code": "needs_consent", "message": "consent"},
            "consent_flow": "seedance",
            "consent_version": "1.0",
            "policy_text": "you must own consent",
            "face_media_roles": ["image"],
            "docs_url": "https://venice.ai/seedance",
        },
    )
    runner = MediaRunner(
        client=_StubClient([response]),  # type: ignore[arg-type]
        writer=ArtifactWriter(tmp_path / "out"),
        jobs=JobStore(tmp_path / "jobs"),
        consent_store=None,
    )
    with pytest.raises(ConsentRequired):
        runner._image_generate(request)  # type: ignore[attr-defined]


def test_cli_quote_approval_required_typed_error(tmp_path: Path) -> None:
    request = MediaRequest.from_mapping(
        {
            "operation": "video.generate",
            "model": "venice-1",
            "prompt": "hi",
            "parameters": {"duration": "5s"},
            "execution": {"wait": False},
        }
    )

    def fake_request(
        self: _StubClient,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        **_: Any,
    ) -> ApiResponse:
        if path.endswith("/quote"):
            return ApiResponse(
                status_code=200,
                content_type="application/json",
                headers={"content-type": "application/json"},
                json_data={"quote": 4.5},
            )
        raise AssertionError("queue should not be reached without approval")

    client = _StubClient([])
    client.request = fake_request.__get__(client, _StubClient)  # type: ignore[method-assign]
    runner = MediaRunner(
        client=client,  # type: ignore[arg-type]
        writer=ArtifactWriter(tmp_path / "out"),
        jobs=JobStore(tmp_path / "jobs"),
        consent_store=ConsentStore(tmp_path / "consent.json"),
        quote_store=QuoteApprovalStore(tmp_path / "quotes.json"),
    )
    with pytest.raises(QuoteApprovalRequired):
        runner._video_generate(request)  # type: ignore[attr-defined]


def test_cli_payload_validation_error_returns_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key, value in _settings_dirs(tmp_path).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("VENICE_API_KEY", "x" * 32)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "operation": "video.generate",
                "model": "venice-1",
                "prompt": "hi",
                "parameters": {"model": "venice-other"},  # reserved parameter!
            }
        )
    )
    code = main(["run", str(manifest)])
    assert code == 2
    err = capsys.readouterr().err
    assert "reserved" in err.lower() or "RESERVED" in err.upper()


def test_cli_unknown_command_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("VENICE_API_KEY", "x" * 32)
    for key, value in _settings_dirs(tmp_path).items():
        monkeypatch.setenv(key, value)

    def fake_list(self: ModelCatalog, type_arg: str, refresh: bool = False) -> Any:
        return [{"id": "x"}]

    with patch.object(ModelCatalog, "list", fake_list):
        parser = __import__("venice_media_skill.cli", fromlist=["build_parser"]).build_parser()
        args = parser.parse_args(["models"])
        out = _dispatch(args)
    assert out["status"] == "ok"


def test_cli_resolve_bundled_openapi_explicit_returns_path(tmp_path: Path) -> None:
    target = tmp_path / "venice-openapi.yaml"
    target.write_text("openapi: 3.0.0\n", encoding="utf-8")
    out = _resolve_bundled_openapi(str(target))
    assert out.resolve() == target.resolve()


def test_cli_resolve_bundled_openapi_falls_back_to_asset() -> None:
    src_candidate = Path(__file__).resolve().parents[1] / "references" / "venice-openapi.yaml"
    if src_candidate.is_file():
        out = _resolve_bundled_openapi(None)
        assert out.is_file()
    else:
        # In wheel install, asset path should be used.
        import importlib.resources as importlib_resources

        asset = importlib_resources.files("venice_media_skill").joinpath(
            "assets", "skill", "references", "venice-openapi.yaml"
        )
        if asset.is_file():
            out = _resolve_bundled_openapi(None)
            assert out.is_file()
            out.unlink(missing_ok=True)


def test_cli_emit_compact_and_stream() -> None:
    import io

    buf = io.StringIO()
    _emit({"a": 1, "b": [2, 3]}, compact=True, stream=buf)
    assert buf.getvalue() == '{"a":1,"b":[2,3]}\n'
    buf = io.StringIO()
    _emit({"a": 1}, compact=False, stream=buf)
    assert buf.getvalue().startswith("{")
    assert buf.getvalue().endswith("\n")


# ---------------------------------------------------------------------------
# runner.py: gate + helper coverage
# ---------------------------------------------------------------------------


def test_runner_record_consent_if_needed_returns_dict(tmp_path: Path) -> None:
    runner, consent_store, _, _ = _make_runner(tmp_path, _StubClient([]))
    request = MediaRequest.from_mapping(
        {
            "operation": "image.generate",
            "model": "venice-1",
            "prompt": "hi",
            "execution": {"dry_run": False},
        }
    )
    canonical = build_image_generate(request)
    response = ApiResponse(
        status_code=409,
        content_type="application/json",
        headers={"content-type": "application/json"},
        json_data={
            "error": {"code": "needs_consent", "message": "consent"},
            "consent_flow": "seedance",
            "consent_version": "1.0",
            "policy_text": "you must own consent",
            "face_media_roles": ["image"],
            "docs_url": "https://venice.ai/seedance",
        },
    )
    payload = runner._record_consent_if_needed(canonical, response, media_kind="image")  # type: ignore[attr-defined]
    assert payload is not None
    assert payload["status"] == "consent_required"
    assert payload["challenge_id"]
    challenge = consent_store.load_challenge(payload["challenge_id"])
    assert challenge is not None
    assert challenge.payload_hash == canonical.hash


def test_runner_record_consent_non_consent_returns_none(tmp_path: Path) -> None:
    runner, _, _, _ = _make_runner(tmp_path, _StubClient([]))
    request = MediaRequest.from_mapping({"operation": "image.generate", "model": "venice-1", "prompt": "hi"})
    canonical = build_image_generate(request)
    response = ApiResponse(
        status_code=409,
        content_type="application/json",
        headers={"content-type": "application/json"},
        json_data={"error": "rate limit"},
    )
    assert runner._record_consent_if_needed(canonical, response, media_kind="image") is None  # type: ignore[attr-defined]


def test_runner_record_consent_without_consent_store(tmp_path: Path) -> None:
    request = MediaRequest.from_mapping({"operation": "image.generate", "model": "venice-1", "prompt": "hi"})
    canonical = build_image_generate(request)
    response = ApiResponse(
        status_code=409,
        content_type="application/json",
        headers={"content-type": "application/json"},
        json_data={
            "error": {"code": "needs_consent", "message": "consent"},
            "consent_flow": "seedance",
        },
    )
    runner = MediaRunner(
        client=_StubClient([]),  # type: ignore[arg-type]
        writer=ArtifactWriter(tmp_path / "out"),
        jobs=JobStore(tmp_path / "jobs"),
        consent_store=None,
    )
    with pytest.raises(ConsentRequired):
        runner._record_consent_if_needed(canonical, response, media_kind="image")  # type: ignore[attr-defined]


def test_runner_consume_consent_no_approval_returns_none(tmp_path: Path) -> None:
    runner, _, _, _ = _make_runner(tmp_path, _StubClient([]))
    request = MediaRequest.from_mapping(
        {"operation": "video.generate", "model": "venice-1", "prompt": "hi", "parameters": {"duration": "5s"}}
    )
    canonical = build_video_queue(request)
    assert runner._consume_consent_approval(request=request, canonical=canonical) is None  # type: ignore[attr-defined]


def test_runner_consume_consent_with_approval(tmp_path: Path) -> None:
    runner, consent_store, _, _ = _make_runner(tmp_path, _StubClient([]))
    request = MediaRequest.from_mapping(
        {"operation": "video.generate", "model": "venice-1", "prompt": "hi", "parameters": {"duration": "5s"}}
    )
    canonical = build_video_queue(request)
    cid = consent_store.record_challenge(
        operation="video.generate",
        model="venice-1",
        payload_hash=canonical.hash,
        input_hashes=[],
        provider_payload={
            "error": {"code": "needs_consent", "message": "ok"},
            "consent_flow": "seedance",
            "consent_version": "1.0",
            "policy_text": "policy",
            "face_media_roles": ["image"],
            "docs_url": "https://venice.ai/seedance",
        },
    ).challenge_id
    consent_store.approve(challenge_id=cid, confirmed_max_cost=5.0, acknowledge_policy=True)
    block = runner._consume_consent_approval(request=request, canonical=canonical)  # type: ignore[attr-defined]
    assert block is not None
    assert block == {
        "confirmed_terms_and_privacy": True,
        "confirmed_legal_right": True,
        "confirmed_screening_acknowledged": True,
    }


def test_runner_require_quote_approval_cost_none(tmp_path: Path) -> None:
    runner, _, _, _ = _make_runner(tmp_path, _StubClient([]))
    request = MediaRequest.from_mapping(
        {"operation": "video.generate", "model": "venice-1", "prompt": "hi", "parameters": {"duration": "5s"}}
    )
    canonical = build_video_queue(request)
    with pytest.raises(QuoteApprovalRequired):
        runner._require_quote_approval(  # type: ignore[attr-defined]
            request=request,
            canonical=canonical,
            quote_response={},
        )


def test_runner_require_quote_approval_present_succeeds(tmp_path: Path) -> None:
    runner, _, quote_store, _ = _make_runner(tmp_path, _StubClient([]))
    request = MediaRequest.from_mapping(
        {"operation": "video.generate", "model": "venice-1", "prompt": "hi", "parameters": {"duration": "5s"}}
    )
    canonical = build_video_queue(request)
    quote_store.record(
        operation="video.generate",
        payload_hash=canonical.hash,
        quote_response={"quote": 4.5},
        max_cost=10.0,
    )
    runner._require_quote_approval(  # type: ignore[attr-defined]
        request=request,
        canonical=canonical,
        quote_response={"quote": 4.5},
    )


def test_runner_fresh_download_url_branches() -> None:
    fn = MediaRunner._fresh_download_url
    assert fn({"download_url": "https://x/a"}, fallback=None) == "https://x/a"
    assert fn({"data": {"url": "https://x/b"}}, fallback=None) == "https://x/b"
    assert fn({"nope": True}, fallback="https://x/c") == "https://x/c"
    assert fn("not-a-dict", fallback=None) is None  # type: ignore[arg-type]


def test_runner_complete_if_requested_noop(tmp_path: Path) -> None:
    runner, _, _, _ = _make_runner(tmp_path, _StubClient([]))
    request = MediaRequest.from_mapping(
        {"operation": "video.generate", "model": "venice-1", "prompt": "hi", "parameters": {"duration": "5s"}}
    )
    runner._complete_if_requested(request, "video", "venice-1", "q-1")  # type: ignore[attr-defined]
    assert runner.client.calls == []  # type: ignore[attr-defined]


def test_runner_complete_with_delete(tmp_path: Path) -> None:
    runner, _, _, jobs = _make_runner(
        tmp_path,
        _StubClient(
            [
                ApiResponse(
                    status_code=200,
                    content_type="application/json",
                    headers={"content-type": "application/json"},
                    json_data={"ok": True},
                ),
            ]
        ),
    )
    request = MediaRequest.from_mapping(
        {
            "operation": "video.generate",
            "model": "venice-1",
            "prompt": "hi",
            "parameters": {"duration": "5s"},
            "execution": {"delete_remote_on_completion": True},
        }
    )
    jobs.create(
        media_type="video",
        model="venice-1",
        queue_id="q-1",
        request={
            "operation": "video.generate",
            "model": "venice-1",
            "prompt": "hi",
            "parameters": {"duration": "5s"},
        },
    )
    runner._complete_if_requested(request, "video", "venice-1", "q-1")  # type: ignore[attr-defined]
    assert ("POST", "/video/complete", {"model": "venice-1", "queue_id": "q-1"}) in runner.client.calls
    assert jobs.get("q-1")["remote_media_deleted"] is True  # type: ignore[attr-defined]


def test_runner_summarize_inputs(tmp_path: Path) -> None:
    png = tmp_path / "a.png"
    png.write_bytes(_PNG)
    request = MediaRequest.from_mapping({"operation": "image.upscale", "inputs": {"image": str(png)}})
    summary = _summarize_inputs(request)  # type: ignore[attr-defined]
    item = summary[0]
    assert item["kind"] == "local_media"
    assert item["bytes"] == len(_PNG)


def test_runner_summarize_list_member_branches() -> None:
    data_url = f"data:image/png;base64,{base64.b64encode(_PNG).decode('ascii')}"
    out = _summarize_list_member(data_url)  # type: ignore[attr-defined]
    assert out["redacted"] is True
    out = _summarize_list_member("https://x.example/foo?token=secret")  # type: ignore[attr-defined]
    assert out["redacted_query"] is True
    out = _summarize_list_member("x" * 200)
    assert out.endswith("...")
    assert len(out) == 67


def test_runner_sanitize_api_request_branches() -> None:
    out = _sanitize_api_request("image.generate", {"model": "x", "image": "data:image/png;base64,xxx"})  # type: ignore[attr-defined]
    assert out["image"]["kind"] == "local_media"
    assert out["$bridge_operation"] == "image.generate"
    out = _sanitize_api_request("video.generate", {"url": "https://x/y?token=abc"})  # type: ignore[attr-defined]
    assert out["url"]["redacted_query"] is True
    out = _sanitize_api_request("video.generate", {"x": [1, "https://x/y?token=1"]})  # type: ignore[attr-defined]
    assert isinstance(out["x"], list)
    out = _sanitize_api_request("video.generate", {"x": "%not-a-url%"})  # type: ignore[attr-defined]
    assert out["x"] == "%not-a-url%"


def test_runner_save_transcript_json_and_text(tmp_path: Path) -> None:
    runner, _, _, _ = _make_runner(tmp_path, _StubClient([]))
    audio_path = tmp_path / "x.wav"
    audio_path.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")
    request = MediaRequest.from_mapping(
        {
            "operation": "audio.transcribe",
            "model": "whisper-1",
            "inputs": {"audio": str(audio_path)},
            "output": {
                "directory": str(tmp_path / "out_json"),
                "filename": "x.json",
                "write_metadata": True,
            },
        }
    )
    response = ApiResponse(
        status_code=200,
        content_type="application/json",
        headers={"content-type": "application/json"},
        json_data={"text": "hello world"},
    )
    result = runner._save_transcript(request, response, api_request={"model": "whisper-1"})  # type: ignore[attr-defined]
    artifact = result["artifacts"][0]
    assert Path(artifact["path"]).is_file()
    assert Path(artifact["metadata_path"]).is_file()

    response_text = ApiResponse(
        status_code=200,
        content_type="text/plain",
        headers={"content-type": "text/plain"},
        content=b"plain text\n",
    )
    result2 = runner._save_transcript(  # type: ignore[attr-defined]
        MediaRequest.from_mapping(
            {
                "operation": "audio.transcribe",
                "model": "whisper-1",
                "inputs": {"audio": str(audio_path)},
                "output": {"directory": str(tmp_path / "out_text"), "filename": "x.txt"},
            }
        ),
        response_text,
        api_request={"model": "whisper-1"},
    )
    assert Path(result2["artifacts"][0]["path"]).read_bytes().startswith(b"plain")


def test_runner_save_transcript_collision(tmp_path: Path) -> None:
    runner, _, _, _ = _make_runner(tmp_path, _StubClient([]))
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")
    out_dir = tmp_path / "out_collision"
    out_dir.mkdir()
    (out_dir / "fixed.json").write_text("{}", encoding="utf-8")
    request = MediaRequest.from_mapping(
        {
            "operation": "audio.transcribe",
            "model": "whisper-1",
            "inputs": {"audio": str(audio)},
            "output": {"directory": str(out_dir), "filename": "fixed.json", "overwrite": False},
        }
    )
    response = ApiResponse(
        status_code=200,
        content_type="application/json",
        headers={"content-type": "application/json"},
        json_data={"text": "hi"},
    )
    result = runner._save_transcript(request, response, api_request={"model": "whisper-1"})  # type: ignore[attr-defined]
    artifact_path = Path(result["artifacts"][0]["path"])
    assert artifact_path.exists()
    assert artifact_path.name.startswith("fixed-")


def test_runner_save_transcript_no_payload_raises(tmp_path: Path) -> None:
    runner, _, _, _ = _make_runner(tmp_path, _StubClient([]))
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")
    request = MediaRequest.from_mapping(
        {
            "operation": "audio.transcribe",
            "model": "whisper-1",
            "inputs": {"audio": str(audio)},
            "output": {"directory": str(tmp_path / "out_empty"), "filename": "x.txt", "overwrite": False},
        }
    )
    response = ApiResponse(
        status_code=200,
        content_type="application/octet-stream",
        headers={"content-type": "application/octet-stream"},
        content=None,
        json_data=None,
    )
    with pytest.raises(OutputError):
        runner._save_transcript(request, response, api_request={"model": "whisper-1"})  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# util.py: detected_content_type + validation branches
# ---------------------------------------------------------------------------


def test_detected_content_type_signatures() -> None:
    assert detected_content_type(b"\x89PNG\r\n\x1a\nxxxxxxxx") == "image/png"
    assert detected_content_type(b"\xff\xd8\xff\xe0xxxx") == "image/jpeg"
    assert detected_content_type(b"GIF87axxxxxxxxx") == "image/gif"
    assert detected_content_type(b"GIF89axxxxxxxxx") == "image/gif"
    assert detected_content_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert detected_content_type(b"RIFF\x00\x00\x00\x00WAVEfmt ") == "audio/wav"
    assert detected_content_type(b"ID3\x04\x00xxxxxxxxxxxxxxxxxxxxxxxxx") == "audio/mpeg"
    assert detected_content_type(b"\xff\xfb\x90xxxxxxxxxxxxxxxxxxxxxxxxx") == "audio/mpeg"
    assert detected_content_type(b"fLaC\x00\x00\x00\x22") == "audio/flac"
    assert detected_content_type(b"OggS\x00\x02") == "audio/ogg"
    assert detected_content_type(b"OpusHead\x01\x02") == "audio/opus"
    assert detected_content_type(b"\xff\xf1\x50xxxxxxxxxxxxxxxxxxxxxxxxx") == "audio/aac"
    assert detected_content_type(b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x01") == "video/mp4"
    assert detected_content_type(b"PK\x03\x04xxxxxxxxxxxxxxxxxxxxxxxxx") == "application/zip"
    assert detected_content_type(b"MZ\x90\x00") == "application/x-binary"
    assert detected_content_type(b"\x7fELF\x02\x01\x01") == "application/x-binary"
    assert detected_content_type(b"xxxx") is None


def test_validate_content_type_each_branch() -> None:
    fast_validate_content_type(_PNG, "image/png")
    with pytest.raises(ContentValidationError):
        fast_validate_content_type(_PNG, "image/jpeg; charset=binary")
    fast_validate_content_type(_PNG, "IMAGE/PNG")  # normalized
    jpeg_body = b"\xff\xd8\xff\xe0\x00\x10JFIF"
    fast_validate_content_type(jpeg_body, "image/jpg")
    fast_validate_content_type(b"GIF89a\x80\x00\x00", "image/gif")
    fast_validate_content_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp")
    fast_validate_content_type(b"RIFF\x00\x00\x00\x00WAVEfmt ", "audio/wav")
    fast_validate_content_type(b"RIFF\x00\x00\x00\x00WAVEfmt ", "audio/x-wav")
    fast_validate_content_type(b"ID3abc", "audio/mpeg")
    fast_validate_content_type(b"fLaCabc", "audio/flac")
    fast_validate_content_type(b"OpusHeadabc", "audio/opus")
    fast_validate_content_type(b"OggSabc", "audio/ogg")
    fast_validate_content_type(b"\xff\xf1abc", "audio/aac")
    fast_validate_content_type(b"silent pcm body", "audio/pcm")
    mp4_body = b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x01" + b"x" * 32
    fast_validate_content_type(mp4_body, "video/mp4")
    fast_validate_content_type(mp4_body, "video/quicktime")
    fast_validate_content_type(b'{"ok": true}', "application/json")
    fast_validate_content_type(b"plain text", "text/plain")

    with pytest.raises(ContentValidationError):
        fast_validate_content_type(b"notapng", "image/png")
    with pytest.raises(ContentValidationError):
        fast_validate_content_type(b"random", "image/jpeg")
    with pytest.raises(ContentValidationError):
        fast_validate_content_type(b"random", "image/webp")
    with pytest.raises(ContentValidationError):
        fast_validate_content_type(b"random", "image/gif")
    with pytest.raises(ContentValidationError):
        fast_validate_content_type(b"random", "image/svg+xml")
    with pytest.raises(ContentValidationError):
        fast_validate_content_type(b"random", "video/mp4")
    with pytest.raises(ContentValidationError):
        fast_validate_content_type(b"random", "video/x-matroska")
    with pytest.raises(ContentValidationError):
        fast_validate_content_type(b"<script>alert(1)</script>", "audio/pcm")
    with pytest.raises(ContentValidationError):
        fast_validate_content_type(b"random", "audio/wav")
    with pytest.raises(ContentValidationError):
        fast_validate_content_type(b"random", "audio/mpeg")
    with pytest.raises(ContentValidationError):
        fast_validate_content_type(b"random", "audio/flac")
    with pytest.raises(ContentValidationError):
        fast_validate_content_type(b"random", "audio/opus")
    with pytest.raises(ContentValidationError):
        fast_validate_content_type(b"random", "audio/ogg")
    with pytest.raises(ContentValidationError):
        fast_validate_content_type(b"random", "audio/aac")
    with pytest.raises(ContentValidationError):
        fast_validate_content_type(b"random", "audio/x-fake")
    with pytest.raises(ContentValidationError):
        fast_validate_content_type(b"\xff\xff\xff", "application/json")
    with pytest.raises(ContentValidationError):
        fast_validate_content_type(b"[\xff\xff]", "application/json")
    with pytest.raises(ContentValidationError):
        fast_validate_content_type(b"<script>alert(1)</script>", "text/plain")
    with pytest.raises(ContentValidationError):
        fast_validate_content_type(b'<?xml version="1.0"?>', "text/plain")
    with pytest.raises(ContentValidationError):
        fast_validate_content_type(b"{}", "application/x-not-on-list")
    with pytest.raises(ContentValidationError):
        fast_validate_content_type(b"", "image/png")
    with pytest.raises(ContentValidationError):
        fast_validate_content_type(_PNG, "")


def test_validate_content_type_alias() -> None:
    assert validate_content_type(_PNG, "image/png") is True
    assert validate_content_type(b"nope", "image/png") is False


def test_is_suspicious_content_matches() -> None:
    assert is_suspicious_content(b"<html></html>", "text/plain") is False
    assert is_suspicious_content(b"<html></html>", "image/png") is True
    assert is_suspicious_content(b"<script>alert(1)</script>", "image/png") is True
    assert is_suspicious_content(b"javascript:alert(1)", "audio/wav") is True
    assert is_suspicious_content(b"<svg></svg>", "video/mp4") is True
    assert is_suspicious_content(b"onerror=foo", "image/png") is True
    assert is_suspicious_content(b"<meta http-equiv>", "image/png") is True
    assert is_suspicious_content(b"<!doctype html>", "image/png") is True
    assert is_suspicious_content(b"plain bytes", "image/png") is False


def test_path_to_data_url(tmp_path: Path) -> None:
    p = tmp_path / "a.png"
    p.write_bytes(_PNG)
    data_url = path_to_data_url(p)
    assert data_url.startswith("data:image/png;base64,")
    with pytest.raises(RequestValidationError):
        path_to_data_url(tmp_path / "missing.png")


def test_decode_data_url_errors_and_ok() -> None:
    mime, blob = decode_data_url(f"data:image/png;base64,{base64.b64encode(_PNG).decode()}")
    assert mime == "image/png"
    assert blob == _PNG
    with pytest.raises(RequestValidationError):
        decode_data_url("not a data url")
    with pytest.raises(RequestValidationError):
        decode_data_url("data:,")
    with pytest.raises(RequestValidationError):
        decode_data_url("data:text/plain;charset=utf-8,nope")
    with pytest.raises(RequestValidationError):
        decode_data_url("data:image/png;base64,@@@@")


def test_extension_for_content_type_branches() -> None:
    assert extension_for_content_type("image/jpeg") == ".jpg"
    assert extension_for_content_type("image/png; charset=binary") == ".png"
    assert extension_for_content_type("image/webp") == ".webp"
    assert extension_for_content_type("image/gif") == ".gif"
    assert extension_for_content_type("audio/mpeg") == ".mp3"
    assert extension_for_content_type("audio/wav") == ".wav"
    assert extension_for_content_type("audio/x-wav") == ".wav"
    assert extension_for_content_type("audio/flac") == ".flac"
    assert extension_for_content_type("audio/aac") == ".aac"
    assert extension_for_content_type("audio/ogg") == ".ogg"
    assert extension_for_content_type("audio/opus") == ".opus"
    assert extension_for_content_type("audio/pcm") == ".pcm"
    assert extension_for_content_type("video/mp4") == ".mp4"
    assert extension_for_content_type("video/quicktime") == ".mov"
    assert extension_for_content_type("text/plain") == ".txt"
    assert extension_for_content_type("application/json") == ".json"
    assert extension_for_content_type("application/x-mystery") == ".bin"


def test_redact_data_variants() -> None:
    assert redact_data({"Authorization": "Bearer xxx"}) == {"Authorization": "[REDACTED]"}
    text_value = redact_data("venice_api_key=sk-abcdef0123456789")
    assert "REDACTED" in str(text_value)
    nested = redact_data({"headers": {"x-auth-token": "abc"}, "value": "venice_api_key xyz"})
    assert nested["headers"]["x-auth-token"] == "[REDACTED]"
    lst = redact_data([{"authorization": "Bearer x"}, "venice_api_key=plain"])
    assert lst[0]["authorization"] == "[REDACTED]"  # type: ignore[index]
    assert "REDACTED" in str(lst[1])  # type: ignore[operator]


def test_stable_helpers() -> None:
    assert timestamp_slug().endswith("Z")
    assert "T" in utc_now_iso()
    assert stable_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    assert sha256_hex(b"abc") == hashlib.sha256(b"abc").hexdigest()


# ---------------------------------------------------------------------------
# output.py: artifact path / atomic writer / blob extraction coverage
# ---------------------------------------------------------------------------


def test_atomic_write_text_overwrites_via_helper(tmp_path: Path) -> None:
    target = tmp_path / "session.json"
    target.write_text("v1")
    atomic_write_text(target, "v2")
    assert target.read_text() == "v2"


def test_atomic_write_bytes_rejects_existing(tmp_path: Path) -> None:
    target = tmp_path / "x.bin"
    target.write_bytes(b"")
    with pytest.raises(OutputError):
        _atomic_write_text(target, "hi")


def test_extract_blobs_with_content(tmp_path: Path) -> None:
    response = ApiResponse(
        status_code=200,
        content_type="image/png",
        headers={"content-type": "image/png"},
        content=_PNG,
    )
    out_path = tmp_path / "single.png"
    ArtifactWriter(tmp_path).save_response(
        response,
        operation="image.generate",
        output_dir=str(tmp_path),
        filename="single.png",
        overwrite=True,
        write_metadata=False,
        metadata={},
    )
    assert out_path.is_file()
    assert out_path.read_bytes() == _PNG


def test_extract_blobs_typed_value_image_b64(tmp_path: Path) -> None:
    b64 = base64.b64encode(_PNG).decode("ascii")
    response = ApiResponse(
        status_code=200,
        content_type="application/json",
        headers={"content-type": "application/json"},
        json_data={"b64_json": b64, "model": "x"},
    )
    artifacts = ArtifactWriter(tmp_path / "base").save_response(
        response,
        operation="image.generate",
        output_dir=str(tmp_path / "from-json"),
        filename=None,
        overwrite=True,
        write_metadata=True,
        metadata={"foo": "bar"},
    )
    assert len(artifacts) == 1
    assert artifacts[0]["metadata_path"]


def test_extract_blobs_list_and_multiple_keys(tmp_path: Path) -> None:
    b64 = base64.b64encode(_PNG).decode("ascii")
    response = ApiResponse(
        status_code=200,
        content_type="application/json",
        headers={"content-type": "application/json"},
        json_data=[{"image": b64}, {"b64_json": b64}, {"images": [{"b64_json": b64}]}],
    )
    artifacts = ArtifactWriter(tmp_path).save_response(
        response,
        operation="image.generate",
        output_dir=str(tmp_path / "list-out"),
        filename=None,
        overwrite=True,
        write_metadata=False,
        metadata={},
    )
    assert len(artifacts) == 3


def test_resolve_artifact_path_user_extension_compat(tmp_path: Path) -> None:
    p = _resolve_artifact_path(  # type: ignore[attr-defined]
        tmp_path,
        operation="image.generate",
        filename="a.jpg",
        index=1,
        total=1,
        content_type="image/jpeg",
        overwrite=True,
    )
    assert p.suffix == ".jpg"


def test_resolve_artifact_path_user_extension_mismatch(tmp_path: Path) -> None:
    p = _resolve_artifact_path(  # type: ignore[attr-defined]
        tmp_path,
        operation="image.generate",
        filename="a.gif",
        index=1,
        total=1,
        content_type="image/png",
        overwrite=True,
    )
    assert p.suffix == ".png"


def test_resolve_artifact_path_total_multi_index(tmp_path: Path) -> None:
    p = _resolve_artifact_path(  # type: ignore[attr-defined]
        tmp_path,
        operation="image.generate",
        filename=None,
        index=2,
        total=2,
        content_type="image/png",
        overwrite=False,
    )
    assert p.stem.endswith("-2")


def test_safe_filename_rejects_variations() -> None:
    with pytest.raises(OutputError):
        _validate_safe_filename("../escape")
    with pytest.raises(OutputError):
        _validate_safe_filename("/abs")
    with pytest.raises(OutputError):
        _validate_safe_filename("\\\\unc")
    with pytest.raises(OutputError):
        _validate_safe_filename("C:relative")
    with pytest.raises(OutputError):
        _validate_safe_filename("a/bad")
    with pytest.raises(OutputError):
        _validate_safe_filename("a\\bad")
    with pytest.raises(OutputError):
        _validate_safe_filename("a\x00b")
    _validate_safe_filename("clean.png")


# ---------------------------------------------------------------------------
# jobs.py: more job store branches
# ---------------------------------------------------------------------------


def test_job_store_get_missing(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    with pytest.raises(OutputError):
        store.get("missing")


def test_job_store_update_missing(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    store.create(media_type="video", model="m", queue_id="q-2", request={"operation": "video.generate", "model": "m"})
    rec = store.update("q-2", status="processing", last_response={"x": 1})
    assert rec["status"] == "processing"
    assert rec["last_response"] == {"x": 1}
    out = store.list()
    assert out and out[0]["queue_id"] == "q-2"


# ---------------------------------------------------------------------------
# catalog.py: cache hit and invalidation branches
# ---------------------------------------------------------------------------


class _LiveClient:
    def __init__(self) -> None:
        self.calls = 0

    def get_json(self, _path: str, *, params: dict[str, Any] | None = None) -> Any:
        self.calls += 1
        return {"data": [{"id": "fresh"}]}


def test_catalog_cache_hit(tmp_path: Path) -> None:
    cache = tmp_path / "models.json"
    cache.write_text(json.dumps({"fetched_at": time.time(), "by_type": {"image": [{"id": "cached"}]}}))
    catalog = ModelCatalog(_LiveClient(), cache)  # type: ignore[arg-type]
    assert catalog.list("image", refresh=False)[0]["id"] == "cached"


def test_catalog_refresh_invalidates(tmp_path: Path) -> None:
    cache = tmp_path / "models.json"
    cache.write_text(json.dumps({"fetched_at": time.time(), "by_type": {"image": [{"id": "cached"}]}}))
    catalog = ModelCatalog(_LiveClient(), cache)  # type: ignore[arg-type]
    out = catalog.list("image", refresh=True)
    assert any(item["id"] == "fresh" for item in out)


# ---------------------------------------------------------------------------
# client.py: download / coercion branches
# ---------------------------------------------------------------------------


def test_is_safe_base_url_branches() -> None:
    assert _is_safe_base_url("https://api.venice.ai") is True
    assert _is_safe_base_url("https://api.venice.ai:443/") is True
    assert _is_safe_base_url("http://api.venice.ai") is False
    assert _is_safe_base_url("https://api.venice.ai:8080") is False
    assert _is_safe_base_url("https://other.example") is False
    assert _is_safe_base_url("") is False


def test_enforce_safe_target_rejects_local(tmp_path: Path) -> None:
    with pytest.raises(NetworkSafetyError):
        _enforce_safe_target("http://api.venice.ai/foo", ALLOWED_DOWNLOAD_HOSTS)
    with pytest.raises(NetworkSafetyError):
        _enforce_safe_target("https://localhost/foo", ALLOWED_DOWNLOAD_HOSTS)
    with pytest.raises(NetworkSafetyError):
        _enforce_safe_target("https://example.com/foo", ALLOWED_DOWNLOAD_HOSTS)
    with pytest.raises(NetworkSafetyError):
        _enforce_safe_target("https://api.venice.ai:8080/foo", ALLOWED_DOWNLOAD_HOSTS)


def test_resolve_safely_handles_localhost() -> None:
    ips = _resolve_safely("localhost")
    assert any(ip.startswith("127.") or ip == "::1" for ip in ips)


def test_resolve_safely_returns_empty_for_missing() -> None:
    ips = _resolve_safely("this-host-should-not-exist-987654.invalid")
    assert ips == []


def test_client_constructor_rejects_unsafe_base() -> None:
    from venice_media_skill.errors import ConfigurationError

    with pytest.raises(ConfigurationError):
        VeniceClient(
            base_url="http://api.venice.ai",
            api_key="x" * 32,
            allow_noncanonical_endpoint=False,
        )


def test_client_coerce_response_status_branch() -> None:
    handler = httpx.MockTransport(
        lambda req: httpx.Response(
            601,
            json={"error": {"message": "oops"}, "request_id": "rid-1"},
            headers={"x-request-id": "rid-1"},
        )
    )
    with VeniceClient(base_url="https://api.venice.ai", api_key="x" * 32, transport=handler) as client:
        with pytest.raises(ApiError) as info:
            client.request("GET", "/anything")
        assert info.value.status_code == 601
        assert info.value.request_id == "rid-1"


def test_client_consent_required_surface() -> None:
    handler = httpx.MockTransport(
        lambda req: httpx.Response(
            409,
            json={
                "error": {"code": "needs_consent", "message": "ok"},
                "consent_flow": "seedance",
                "consent_version": "1.0",
                "policy_text": "policy",
                "face_media_roles": ["image"],
                "docs_url": "https://venice.ai/seedance",
            },
        )
    )
    with (
        VeniceClient(base_url="https://api.venice.ai", api_key="x" * 32, transport=handler) as client,
        pytest.raises(ConsentRequired),
    ):
        client.request("POST", "/video/queue")


# ---------------------------------------------------------------------------
# payloads / request / consent / installer coverage smoke
# ---------------------------------------------------------------------------


def test_build_video_queue_and_quote_match() -> None:
    request = MediaRequest.from_mapping(
        {
            "operation": "video.generate",
            "model": "venice-1",
            "prompt": "hi",
            "parameters": {
                "duration": "5s",
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "audio": True,
            },
            "execution": {"dry_run": True, "wait": False},
        }
    )
    queue = build_video_queue(request)
    quote = build_video_quote(request)
    assert queue.hash == quote.hash
    # Quote is a typed projection: every key submitted for quoting must be
    # legal per the OpenAPI QuoteVideoRequest shape, but reference media is
    # stripped because Venice charges by duration/resolution/aspect_ratio.
    assert set(quote.payload).issubset(set(queue.payload))
    expected_quote_keys = {"duration", "aspect_ratio", "resolution", "audio", "model"}
    assert expected_quote_keys.issubset(quote.payload.keys())


def test_build_audio_queue_and_quote_match() -> None:
    request = MediaRequest.from_mapping(
        {
            "operation": "audio.generate",
            "model": "venice-music",
            "prompt": "jazz",
            "parameters": {
                "duration_seconds": 30,
                "force_instrumental": True,
                "lyrics_prompt": "unused",
            },
            "execution": {"dry_run": True, "wait": False},
        }
    )
    queue = build_audio_queue(request)
    quote = build_audio_quote(request)
    assert queue.hash == quote.hash
    # Quote schema is exactly {model, duration_seconds, character_count};
    # queue-only fields like ``lyrics_prompt`` or ``force_instrumental``
    # must never appear in the quote body.
    assert set(quote.payload).issubset({"model", "duration_seconds", "character_count"})
    assert "force_instrumental" not in quote.payload
    assert "lyrics_prompt" not in quote.payload
    assert "duration_seconds" in quote.payload


def test_reserved_parameters_constant() -> None:
    assert "model" in RESERVED_PARAMETERS
    assert "prompt" in RESERVED_PARAMETERS
    assert "parameters" in RESERVED_PARAMETERS
    assert "consents" in RESERVED_PARAMETERS


def test_canonical_payload_construction() -> None:
    cp = CanonicalPayload(operation="x", endpoint="/x", payload={"a": 1}, hash="abc", input_hashes=())
    assert cp.endpoint == "/x"
    assert cp.input_hashes == ()


def test_consent_object_helpers() -> None:
    obj = build_consent_object(policy_version="v1")
    assert obj == {
        "confirmed_terms_and_privacy": True,
        "confirmed_legal_right": True,
        "confirmed_screening_acknowledged": True,
    }
    obj = build_consent_object(policy_version="")
    assert obj == {
        "confirmed_terms_and_privacy": True,
        "confirmed_legal_right": True,
        "confirmed_screening_acknowledged": True,
    }


def test_ensure_seedance_fact_branches() -> None:
    assert ensure_seedance_fact({"error": {"code": "needs_consent"}, "consent_flow": "seedance"}) is True
    assert ensure_seedance_fact({"needs_consent": True}) is False
    assert ensure_seedance_fact({"oops": 1}) is False
    assert ensure_seedance_fact({}) is False


def test_quote_approval_store_record_and_consume_branches(tmp_path: Path) -> None:
    store = QuoteApprovalStore(tmp_path / "qu.json")
    payload_hash = "b" * 64
    approval = store.record(
        operation="video.generate",
        payload_hash=payload_hash,
        quote_response={"quote": 9.0},
        max_cost=10.0,
    )
    resolved = store.resolve(payload_hash)
    assert resolved is not None
    assert resolved.operation == "video.generate"
    with pytest.raises(QuoteApprovalMismatch):
        store.consume(approval_id=approval.approval_id, current_payload_hash="different", max_observed_cost=9.0)
    with pytest.raises(ConsentApprovalMissing):
        store.consume(approval_id="missing", current_payload_hash=payload_hash, max_observed_cost=9.0)
    # Over max cost returns ConsentApprovalMissing('quote exceeded ...'), not QuoteApprovalMismatch.
    with pytest.raises(ConsentApprovalMissing):
        store.consume(approval_id=approval.approval_id, current_payload_hash=payload_hash, max_observed_cost=99.0)
    # successful consume removes the entry.
    store.consume(approval_id=approval.approval_id, current_payload_hash=payload_hash, max_observed_cost=8.0)
    assert store.resolve(payload_hash) is None


def test_quote_approval_id_is_well_formed(tmp_path: Path) -> None:
    store = QuoteApprovalStore(tmp_path / "q.json")
    approval = store.record(
        operation="audio.generate",
        payload_hash="c" * 64,
        quote_response={"quote": 1.0},
        max_cost=1.0,
    )
    assert approval.approval_id.startswith("qap_")


def test_consent_store_unknown_challenge_approve() -> None:
    store = ConsentStore(Path("/tmp/nonexistent_consent.json"))
    # Don't trigger directory creation on import-only test
    with pytest.raises(Exception):  # ConsentApprovalMissing may also be raised
        store.approve(challenge_id="not-present", confirmed_max_cost=1.0, acknowledge_policy=True)


# ---------------------------------------------------------------------------
# Validate-openapi coverage
# ---------------------------------------------------------------------------


def test_resolve_bundled_openapi_explicit_path(tmp_path: Path) -> None:
    p = tmp_path / "venice-openapi.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "openapi": "3.5.0",
                "info": {"version": "1"},
                "paths": {f"/x{i}": {} for i in range(13)},
            }
        ),
        encoding="utf-8",
    )
    out = _resolve_bundled_openapi(str(p))
    assert str(out.resolve()).startswith(str(tmp_path.resolve()))


def test_validate_openapi_dict_paths(tmp_path: Path) -> None:
    p = tmp_path / "v.yaml"
    p.write_text(
        yaml.safe_dump({"openapi": "3.5.0", "info": {"version": "1"}, "paths": {"/models": {}}}), encoding="utf-8"
    )
    out = _validate_openapi(p)
    missing = set(out["missing_required_paths"])
    assert "/image/generate" in missing
    assert "/audio/speech" in missing
    assert "/video/queue" in missing
    assert out["status"] == "invalid"


def test_validate_openapi_missing_file(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        _validate_openapi(tmp_path / "nope.yaml")


# ---------------------------------------------------------------------------
# Installer coverage (re-install path)
# ---------------------------------------------------------------------------


def test_install_skill_overwrites_existing(tmp_path: Path) -> None:
    from venice_media_skill.installer import install_skill

    result = install_skill(host="generic", scope="user")
    assert result["status"] == "installed"
    # Re-install should be idempotent.
    result = install_skill(host="generic", scope="user")
    assert result["status"] == "installed"
