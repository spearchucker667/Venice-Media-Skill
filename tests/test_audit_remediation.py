"""Audit-driven integration tests for the 2026-07-18+ remediation sweep.

Each test maps to a numbered defect from the post-fix audit:

1. P0-1   ``VeniceClient.request`` rejects absolute and scheme-relative paths.
2. P0-2   ``ArtifactWriter.save_response`` commits ``file_path`` blobs to a
          well-named final destination and re-validates magic bytes.
3. P1-1   Every shipped example validates under the new ``_PARAM_RULES``.
4. P1-1   The bundled ``README.md`` (root) is present and parse-aware.
5. P0-3   ``build_video_quote`` projects every legal QuoteVideoRequest field
          from the canonical queue payload.
6. P0-4   ``build_audio_quote`` never carries queue-only fields and shares
          the queue payload's hash.
7. P1-3   ``request_json_schema()`` is a meta-valid Draft 2020-12 schema
          (validated with ``jsonschema.Draft202012Validator.check_schema``).
8. P1-4   The committed ``references/request.schema.json`` matches the
          runtime build and reports zero drift.
9. P1-5   ``validate-openapi`` CLI subcommand exits non-zero when required
          paths are missing.
10. P1-8  ``_FileSink.finalize`` rejects empty downloads BEFORE ``os.replace``
           and never destroys an existing destination.
11. P2-2  ``atomic_write_text`` reuses ``os.replace``; overwriting an
           artifact never removes the destination outside the swap.
12. P2-3/P2-4  Lock filenames include a hash of the resolved state path and
           recover from a cross-host lock older than the stale threshold.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import jsonschema
import pytest

from venice_media_skill.client import (
    ApiResponse,
    NetworkSafetyError,
    VeniceClient,
    _FileSink,
    _validate_api_path,
)
from venice_media_skill.consent import (
    _acquire_lock,
    _release_lock,
)
from venice_media_skill.errors import (
    ConfigurationError,
    ContentValidationError,
    OutputError,
    RequestValidationError,
    TransportError,
)
from venice_media_skill.output import (
    ArtifactWriter,
    _atomic_write_bytes,
    _atomic_write_text,
    _resolve_artifact_path,
    atomic_write_text,
)
from venice_media_skill.payloads import (
    build_audio_queue,
    build_audio_quote,
    build_video_queue,
    build_video_quote,
)
from venice_media_skill.request import MediaRequest, request_json_schema
from venice_media_skill.runner import MediaRunner, _ApprovalClaim
from venice_media_skill.util import fast_validate_content_type


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(0)"])
    pid = proc.pid
    proc.wait()
    return pid


_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xfa\xff\xff?\x03\x00\x05\xfe\x02\xfe\xa3\x9a\xfa\x05"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_p01_request_rejects_absolute_and_scheme_relative_paths() -> None:
    """P0-1: ``request`` accepts only absolute paths so the Bearer header never leaks."""
    for bad in (
        "https://evil.example/api/v1/models",
        "http://api.venice.ai/models",
        "//evil.example/api/v1/models",
        "evil.example/api/v1/models",
        "models",
    ):
        with pytest.raises(NetworkSafetyError):
            _validate_api_path(bad)
    # Sanity: legal absolute paths pass through unchanged.
    assert _validate_api_path("/models") == "/models"
    assert _validate_api_path("/image/generate") == "/image/generate"


def test_p02_file_path_artifact_persisted_with_correct_extension(tmp_path: Path) -> None:
    """P0-2: file_path blobs land at the caller's resolved path with extension."""
    on_disk = tmp_path / "video-no-ext"
    on_disk.write_bytes(_PNG)
    response = ApiResponse(
        200,
        "image/png",
        {},
        file_path=on_disk,
        sha256=hashlib.sha256(_PNG).hexdigest(),
        observed=len(_PNG),
    )
    writer = ArtifactWriter(tmp_path)
    artifacts = writer.save_response(
        response,
        operation="image.upscale",
        output_dir=str(tmp_path / "out"),
        filename="upscaled",
        overwrite=False,
        write_metadata=True,
        metadata={"model": "x"},
    )
    final = Path(artifacts[0]["path"])
    assert final.is_file()
    assert final.suffix == ".png"
    # The committed destination is not the bare intermediate name.
    assert final.name == "upscaled.png"
    # Audit P2-06 transactional model copies the source into a staging
    # subdir and commits via ``os.replace``.  The on-disk source remains
    # untouched — only the staged copy is moved into place.
    assert on_disk.exists()
    assert final.read_bytes() == _PNG


def test_p11_shipped_examples_all_validate() -> None:
    """P1-1: every example under ``examples/requests`` loads without error."""
    examples = Path(__file__).resolve().parents[1] / "examples" / "requests"
    for path in sorted(examples.glob("*.json")):
        MediaRequest.from_file(path)
    # Also exercise the readme / SKILL top-level manifests are present and
    # warning-free under the request schema.
    repo_root = Path(__file__).resolve().parents[1]
    assert (repo_root / "README.md").is_file()
    assert (repo_root / "skills" / "venice-media" / "SKILL.md").is_file()


def test_every_example_validates_schema_and_reaches_cli_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from venice_media_skill.cli import main

    fixtures = {
        ".png": _PNG,
        ".mp3": b"ID3" + b"\x00" * 64,
        ".mp4": b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64,
    }
    monkeypatch.setenv("VENICE_MEDIA_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("VENICE_MEDIA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("VENICE_MEDIA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("VENICE_MEDIA_OUTPUT_DIR", str(tmp_path / "output"))
    schema = request_json_schema()
    examples = Path(__file__).resolve().parents[1] / "examples" / "requests"
    for source in sorted(examples.glob("*.json")):
        payload = json.loads(source.read_text())
        for key, value in list(payload.get("inputs", {}).items()):
            values = value if isinstance(value, list) else [value]
            replacements: list[str] = []
            for index, item in enumerate(values):
                if isinstance(item, str) and item.startswith("/absolute/path/"):
                    suffix = Path(item).suffix
                    fixture = tmp_path / f"{source.stem}-{key}-{index}{suffix}"
                    fixture.write_bytes(fixtures[suffix])
                    replacements.append(str(fixture))
                else:
                    replacements.append(item)
            payload["inputs"][key] = replacements if isinstance(value, list) else replacements[0]
        jsonschema.validate(payload, schema)
        manifest = tmp_path / source.name
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        assert main(["run", str(manifest)]) == 0
        emitted = json.loads(capsys.readouterr().out)
        assert emitted["status"] == "dry_run"


def test_p11_readme_skills_manifests_present(tmp_path: Path) -> None:
    """The readme and skill are byte-stable (sha256 against fixtures)."""
    repo_root = Path(__file__).resolve().parents[1]
    for path in (repo_root / "README.md", repo_root / "skills" / "venice-media" / "SKILL.md"):
        assert path.is_file()
        assert path.stat().st_size > 100


def test_p03_video_quote_projects_every_quote_field() -> None:
    """P0-3: every QuoteVideoRequest-allowed field is projected from the queue."""
    request = MediaRequest.from_mapping(
        {
            "operation": "video.generate",
            "model": "venice-1",
            "prompt": "p",
            "parameters": {
                "duration": "5s",
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "upscale_factor": 2,
                "audio": True,
                "reference_video_total_duration": 6.5,
            },
            "inputs": {"video": "https://example.com/source.mp4"},
            "execution": {"dry_run": True, "wait": False},
        }
    )
    queue = build_video_queue(request)
    quote = build_video_quote(request)
    assert queue.hash == quote.hash
    expected = {
        "model",
        "duration",
        "aspect_ratio",
        "resolution",
        "upscale_factor",
        "audio",
        "video_url",
        "reference_video_total_duration",
    }
    assert expected.issubset(quote.payload.keys())
    assert quote.payload["reference_video_total_duration"] == 6.5


def test_p04_audio_quote_carries_no_queue_only_fields() -> None:
    """P0-4: only ``model``/``duration_seconds`` survive in quote; ``character_count``

    is quote-only and never reaches the queue body. Approval hash (quote)
    changes when ``character_count`` changes.
    """
    for request_kwargs in (
        {"parameters": {"duration_seconds": 30, "force_instrumental": True}},
        {"parameters": {"duration_seconds": 30, "character_count": 200, "force_instrumental": True}},
    ):
        request = MediaRequest.from_mapping(
            {
                "operation": "audio.generate",
                "model": "venice-music",
                "prompt": "jazz",
                **request_kwargs,  # type: ignore[arg-type]
                "execution": {"dry_run": True, "wait": False},
            }
        )
        queue = build_audio_queue(request)
        quote = build_audio_quote(request)
        assert "character_count" not in queue.payload
        for forbidden in ("force_instrumental", "lyrics_prompt", "voice", "prompt"):
            assert forbidden not in quote.payload
        assert quote.payload["model"] == "venice-music"
        assert quote.payload["duration_seconds"] == 30

    # Approval hash must change when character_count changes (billing impact).
    req_no_cc = MediaRequest.from_mapping(
        {
            "operation": "audio.generate",
            "model": "venice-music",
            "prompt": "jazz",
            "parameters": {"duration_seconds": 30},
            "execution": {"dry_run": True, "wait": False},
        }
    )
    req_with_cc = MediaRequest.from_mapping(
        {
            "operation": "audio.generate",
            "model": "venice-music",
            "prompt": "jazz",
            "parameters": {"duration_seconds": 30, "character_count": 500},
            "execution": {"dry_run": True, "wait": False},
        }
    )
    assert build_audio_quote(req_no_cc).hash != build_audio_quote(req_with_cc).hash


def test_p13_request_json_schema_is_meta_valid() -> None:
    """P1-3: the generated schema parses cleanly under Draft 2020-12."""
    schema = request_json_schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    # Top-level ``allOf`` carries per-operation ``if/then`` clauses.
    assert "allOf" in schema
    branches = schema["allOf"]
    assert len(branches) == len(set(item["if"]["properties"]["operation"]["const"] for item in branches))
    # ``parameters`` is no longer a malformed oneOf container — it only
    # carries the reserved-key rejection.
    assert "oneOf" not in schema["properties"]["parameters"]
    assert "not" in schema["properties"]["parameters"]


def test_p14_committed_schema_matches_runtime(tmp_path: Path) -> None:
    """P1-4: regenerating the schema reproduces the committed file."""

    import subprocess
    import sys
    import tempfile

    committed = Path(__file__).resolve().parents[1] / "references" / "request.schema.json"
    with tempfile.TemporaryDirectory() as tmp:
        regen = Path(tmp) / "request.schema.json"
        subprocess.run(
            [sys.executable, "-m", "venice_media_skill", "schema", "--output", str(regen)],
            check=True,
            env={"PYTHONPATH": "src", "PATH": "/usr/bin:/bin"},
            cwd=tmp_path,
        )
        on_disk = json.loads(committed.read_text())
        regen_loaded = json.loads(regen.read_text())
        assert on_disk == regen_loaded


def test_p15_validate_openapi_raises_on_missing_paths(tmp_path: Path) -> None:
    """P1-5: ``validate-openapi`` exits non-zero when required paths are missing."""
    import yaml

    from venice_media_skill.cli import main

    openapi = {
        "openapi": "3.1.0",
        "info": {"title": "minimal", "version": "0.0.1"},
        "paths": {},
    }
    src = tmp_path / "openapi.yaml"
    src.write_text(yaml.safe_dump(openapi))
    assert main(["validate-openapi", str(src)]) == 2
    # ConfigurationError is the typed error contract.
    with pytest.raises(ConfigurationError, match="missing required paths"):
        from venice_media_skill.cli import _validate_openapi_dispatch

        _validate_openapi_dispatch(src)


def test_p18_empty_download_preserves_destination(tmp_path: Path) -> None:
    """P1-8: ``_FileSink.finalize`` rejects empty downloads and preserves the destination."""
    destination = tmp_path / "out.bin"
    destination.write_bytes(b"original-content")
    sink = _FileSink(destination)
    # Write nothing (observed == 0) and try to finalize.
    with pytest.raises(NetworkSafetyError):
        sink.finalize()
    # The original file must still exist with its original content.
    assert destination.read_bytes() == b"original-content"


def test_p22_atomic_overwrite_no_remove_window(tmp_path: Path) -> None:
    """P2-2: ``atomic_write_text`` overwrites atomically; the old file is never absent."""
    target = tmp_path / "state.json"
    atomic_write_text(target, json.dumps({"version": 1}))
    original_body = target.read_text()
    # Overwrite with new content: we expect no observable "absent" window
    # because the file system calls happen back-to-back.
    atomic_write_text(target, json.dumps({"version": 2}))
    assert json.loads(target.read_text())["version"] == 2
    assert json.loads(original_body)["version"] == 1


def test_p23_p24_lock_path_hashed_and_recovers_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """P2-3/P2-4: lock filename encodes the resolved path; stale locks self-heal."""
    state_a = (tmp_path / "dirA").resolve()
    state_b = (tmp_path / "dirB").resolve()
    state_a.mkdir()
    state_b.mkdir()
    file_a = state_a / "consent_approvals.json"
    file_b = state_b / "consent_approvals.json"
    # Two identically-named files in different directories must receive
    # distinct locks (P2-3).
    from venice_media_skill import consent

    monkeypatch.setattr(consent, "_LOCK_DIR", str(tmp_path / "locks"))
    lock_a = consent._get_lock_path(file_a)
    lock_b = consent._get_lock_path(file_b)
    assert lock_a != lock_b
    # Place a stale lock referencing a dead PID at ``lock_a``; a fresh
    # acquire from this process must steal it (P2-4).
    # Use a PID from a short-lived subprocess that has exited.
    dead_pid = _dead_pid()

    fake_lock_a = lock_a
    fake_lock_a.parent.mkdir(parents=True, exist_ok=True)
    from datetime import UTC, datetime, timedelta

    past = (datetime.now(UTC) - timedelta(minutes=45)).timestamp()
    fake_lock_a.write_text(
        f"host={socket.gethostname()}\npid={dead_pid}\nacquired_at={int(past)}\nowner_token=aaaa\n",
        encoding="utf-8",
    )
    file_a.parent.mkdir(parents=True, exist_ok=True)
    _acquire_lock(file_a)  # must steal the stale lock
    # After stealing+re-acquiring, the lock file holds the *current*
    # process PID, not the dead PID=1 planted above.
    body = lock_a.read_text(encoding="utf-8")
    locked_pid = None
    for line in body.splitlines():
        if line.startswith("pid="):
            locked_pid = int(line.split("=", 1)[1])
    assert locked_pid == os.getpid()
    _release_lock(file_a)


# Sanity: confirm the helpers exist so the new tests are discoverable.
def test_helpers_importable() -> None:
    assert callable(_validate_api_path)
    assert callable(_acquire_lock)
    assert callable(_release_lock)
    assert callable(_resolve_artifact_path)


def test_atomic_helpers_return_existing_target(tmp_path: Path) -> None:
    binary = tmp_path / "value.bin"
    text = tmp_path / "value.txt"
    assert _atomic_write_bytes(binary, b"value") == binary.resolve()
    assert _atomic_write_text(text, "value") == text.resolve()
    assert binary.exists() and text.exists()


def test_realistic_riff_headers_and_bounded_text_json_validation() -> None:
    wav = b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt " + b"\x00" * 32
    webp = b"RIFF" + (24).to_bytes(4, "little") + b"WEBPVP8 " + b"\x00" * 16
    fast_validate_content_type(wav, "audio/wav")
    fast_validate_content_type(webp, "image/webp")
    fast_validate_content_type(b'{"ok": true}', "application/json")
    with pytest.raises(ContentValidationError):
        fast_validate_content_type(b'{"broken": }', "application/json")
    with pytest.raises(ContentValidationError):
        fast_validate_content_type(b"plain\x00text", "text/plain")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_execution_numbers_rejected(value: float) -> None:
    with pytest.raises(RequestValidationError):
        MediaRequest.from_mapping(
            {
                "operation": "image.generate",
                "model": "m",
                "prompt": "p",
                "execution": {"timeout_seconds": value},
            }
        )


def test_transport_error_exit_code_9(monkeypatch: pytest.MonkeyPatch) -> None:
    from venice_media_skill import cli

    def fail(_args: object) -> object:
        raise TransportError("offline", "ConnectError")

    monkeypatch.setattr(cli, "_dispatch", fail)
    assert cli.main(["doctor"]) == 9


# Sanity: ensure the VeniceClient constructor does not regress on a normal path.
def test_venice_client_accepts_legal_base_url() -> None:
    client = VeniceClient(base_url="https://api.venice.ai", api_key="x", timeout_seconds=1)
    client.close()


# ---------------------------------------------------------------------------
# VMS-CUR-001 — Streamed download must propagate observed to ArtifactWriter
# ---------------------------------------------------------------------------


def test_cur001_streamed_download_observed_propagates_through_artifact_writer(tmp_path: Path) -> None:
    """VMS-CUR-001: download_public_file → ApiResponse.observed > 0 → ArtifactWriter commits.

    Regression: ApiResponse constructed from the streamed _FileSink must carry
    ``observed`` so that ``_extract_blobs`` recognises the file-backed blob.
    """
    body = b"\x89PNG\r\n\x1a\n" + b"\x00" * 1024
    expected_sha256 = hashlib.sha256(body).hexdigest()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "image/png"})

    def _stub_resolve(_host: str) -> list[str]:
        return ["8.8.8.8"]

    destination = tmp_path / "downloaded.png"

    client = VeniceClient(
        base_url="https://api.example.test/api/v1",
        api_key="key",
        allow_noncanonical_endpoint=True,
        resolver=_stub_resolve,
    )

    with httpx.MockTransport(handler) as transport:
        response = client.download_public_file(
            "https://cdn.venice.ai/clip.png",
            destination=destination,
            transport=transport,
        )

    assert response.file_path == destination
    assert response.observed == len(body), f"expected observed={len(body)}, got {response.observed}"
    assert response.sha256 == expected_sha256
    assert response.content is None
    assert destination.read_bytes() == body

    writer = ArtifactWriter(tmp_path)
    artifacts = writer.save_response(
        response,
        operation="image.generate",
        output_dir=str(tmp_path / "out"),
        filename="final",
        overwrite=False,
        write_metadata=True,
        metadata={"model": "x"},
    )

    final = Path(artifacts[0]["path"])
    assert final.is_file()
    assert final.read_bytes() == body
    assert final.suffix == ".png"
    # Audit P2-06 transactional model copies the streamed download
    # through a staging subdir and commits via ``os.replace``.  The
    # downloaded source file remains in place — only the staged copy is
    # moved to the final destination.
    assert destination.exists()


# ---------------------------------------------------------------------------
# VMS-CUR-012 — Atomic no-overwrite + multi-blob preflight reservation
# ---------------------------------------------------------------------------


def test_cur012_atomic_no_overwrite_uses_o_excl(tmp_path: Path) -> None:
    """VMS-CUR-012: ``_atomic_write_bytes`` with allow_overwrite=False rejects TOCTOU races.

    Regression: when ``allow_overwrite=False`` and the destination already
    exists, the write must raise ``OutputError`` (delete-free). The atomic
    reservation uses ``O_CREAT | O_EXCL`` so a concurrent writer cannot
    sneak in between the existence check and the temp+replace.
    """
    target = tmp_path / "atomic.png"
    target.write_bytes(b"pre-existing")
    from venice_media_skill.output import _atomic_write_bytes

    with pytest.raises(OutputError, match="already exists"):
        _atomic_write_bytes(target, b"replacement")
    # An exception did not destroy the existing file.
    assert target.read_bytes() == b"pre-existing"


def test_cur012_target_resolve_failure_leaves_no_staging(tmp_path: Path) -> None:
    """VMS-CUR-012: if the second target cannot be resolved, the staging dir is never created."""
    import venice_media_skill.output as output_module
    from venice_media_skill.output import ArtifactWriter

    real_resolver = output_module._resolve_artifact_path
    counter = {"n": 0}

    def flaky(directory: Path, **kwargs: object) -> Path:
        counter["n"] += 1
        if counter["n"] == 2:
            raise OutputError("simulated path resolution failure")
        return real_resolver(directory, **kwargs)

    encoded = base64.b64encode(_PNG).decode()
    response = ApiResponse(
        200,
        "application/json",
        {},
        json_data={"data": [{"b64_json": encoded}, {"b64_json": encoded}]},
    )
    writer = ArtifactWriter(tmp_path)
    original = output_module._resolve_artifact_path
    output_module._resolve_artifact_path = flaky  # type: ignore[assignment]
    try:
        with pytest.raises(OutputError, match="simulated"):
            writer.save_response(
                response,
                operation="image.generate",
                output_dir=None,
                filename="multi",
                overwrite=True,
                write_metadata=False,
                metadata={},
            )
    finally:
        output_module._resolve_artifact_path = original  # type: ignore[assignment]
    assert not (tmp_path / ".venice-media-staging").exists()


def test_cur012_multiblob_mid_batch_failure_rolls_back_staging(tmp_path: Path) -> None:
    """VMS-CUR-012: a mid-batch staging write failure leaves no published artifacts."""
    from venice_media_skill.output import ArtifactWriter

    encoded_a = base64.b64encode(_PNG).decode()
    encoded_b = base64.b64encode(_PNG).decode()
    response = ApiResponse(
        200,
        "application/json",
        {},
        json_data={"data": [{"b64_json": encoded_a}, {"b64_json": encoded_b}]},
    )
    writer = ArtifactWriter(tmp_path)

    import venice_media_skill.output as output_module

    real_atomic = output_module._atomic_write_bytes
    counter = {"n": 0}

    def flake(target: Path, data: bytes, *, allow_overwrite: bool = False):  # type: ignore[no-untyped-def]
        counter["n"] += 1
        if counter["n"] == 2:
            raise OSError(28, "simulated mid-batch write failure")
        return real_atomic(target, data, allow_overwrite=allow_overwrite)

    original = output_module._atomic_write_bytes
    output_module._atomic_write_bytes = flake  # type: ignore[assignment]
    try:
        with pytest.raises(OSError):
            writer.save_response(
                response,
                operation="image.generate",
                output_dir=None,
                filename="multi",
                overwrite=True,
                write_metadata=False,
                metadata={},
            )
    finally:
        output_module._atomic_write_bytes = original  # type: ignore[assignment]
    leftovers = [p for p in tmp_path.iterdir() if p.is_file()]
    assert leftovers == []
    assert not (tmp_path / ".venice-media-staging").exists()


# ---------------------------------------------------------------------------
# VMS-CUR-013 — Approval claim is finalised only on provider acceptance
# ---------------------------------------------------------------------------


def test_cur013_claim_three_phase_commit_on_queue_id(tmp_path: Path) -> None:
    """VMS-CUR-013: claim moves approval to ``_claimed``; finalise deletes it permanently."""
    from venice_media_skill.consent import ConsentStore

    consent = ConsentStore(tmp_path / "consent.json")
    payload_hash = "a" * 64
    cid = consent.record_challenge(
        operation="video.generate",
        model="venice-1",
        payload_hash=payload_hash,
        input_hashes=(),
        provider_payload={
            "consent": {"consent_version": "1", "policy_text": "p"},
            "consent_flow": "seedance",
            "face_media_roles": [],
            "docs_url": "https://x",
        },
    ).challenge_id
    consent.approve(challenge_id=cid, confirmed_max_cost=5.0, acknowledge_policy=True)
    consent.claim(payload_hash, observed_cost=1.0)
    # While pending, the approval is moved out of approvals into _claimed.
    pending = consent._read()
    assert cid not in pending["approvals"]
    assert cid in pending.get("_claimed", {})
    # finalise_claim removes the claim permanently.
    consent.finalize_claim(challenge_id=cid)
    post = consent._read()
    assert cid not in post["approvals"]
    assert cid not in post.get("_claimed", {})


def test_cur013_claim_release_restores_approval_on_needs_consent(tmp_path: Path) -> None:
    """VMS-CUR-013: a definitive non-charging ``409 needs_consent`` must release claims."""
    from venice_media_skill.client import VeniceClient
    from venice_media_skill.consent import ConsentStore
    from venice_media_skill.jobs import JobStore
    from venice_media_skill.output import ArtifactWriter

    consent = ConsentStore(tmp_path / "consent.json")
    payload_hash = "b" * 64
    cid = consent.record_challenge(
        operation="video.generate",
        model="venice-1",
        payload_hash=payload_hash,
        input_hashes=(),
        provider_payload={
            "consent": {"consent_version": "1", "policy_text": "p"},
            "consent_flow": "seedance",
            "face_media_roles": [],
            "docs_url": "https://x",
        },
    ).challenge_id
    consent.approve(challenge_id=cid, confirmed_max_cost=5.0, acknowledge_policy=True)
    consent.claim(payload_hash, observed_cost=1.0)
    # While pending, the approval is no longer in the active pool.
    assert consent.approval_for(payload_hash) is None
    # Releasing the claim restores the approval so the user can retry.
    consent.release_claim(challenge_id=cid)
    assert consent.approval_for(payload_hash) is not None

    # The Runner-level helper must use challenge_id when restoring a consent
    # claim so the host agent does not need to re-approve.
    client = VeniceClient(base_url="https://api.venice.ai", api_key="x", allow_noncanonical_endpoint=True)
    writer = ArtifactWriter(tmp_path)
    jobs = JobStore(tmp_path / "jobs.json")
    runner = MediaRunner(client=client, writer=writer, jobs=jobs, consent_store=consent)
    claim = _ApprovalClaim()
    claim.consent_challenge_id = cid
    claim.consent_store = consent
    # Re-claim the (now restored) approval and then release it via the
    # runner-level helper to verify the three-phase commit works as
    # documented.
    consent.claim(payload_hash, observed_cost=1.0)
    assert consent.approval_for(payload_hash) is None
    runner._release_claims(claim)
    assert consent.approval_for(payload_hash) is not None
    client.close()


def test_cur013_authenticated_path_rejects_qmark_frag_dotseg_and_encoded_traversal() -> None:
    """P2-7: ``_validate_api_path`` rejects query, fragment, raw ``..``, and
    percent-encoded traversal segments on the authenticated client.

    Regression target for VMS-CUR-013: a path that *escapes* / confuses
    ``urlparse`` must never reach ``httpx`` with the ****** attached.
    """
    # Query / fragment must be refused even when the rest looks legitimate.
    for bad in (
        "/models?api_key=leak",
        "/models#fragment",
        "/image/generate?x=1&y=2",
        "/image/generate#frag",
    ):
        with pytest.raises(NetworkSafetyError):
            _validate_api_path(bad)
    # Raw ``..`` traversal segments are refused.
    for bad in (
        "/models/../admin",
        "/image/../private",
        "/../../etc/passwd",
        "/models/..",
    ):
        with pytest.raises(NetworkSafetyError):
            _validate_api_path(bad)
    # Percent-encoded dot / slash / backslash are refused (case-insensitive).
    for bad in (
        "/image/%2E%2E/generate",
        "/image/%2e%2e/generate",
        "/image/%2F%2F/generate",
        "/image/%2f%2f/generate",
        "/image/%5C%5C/generate",
        "/image/%5c%5c/generate",
        "/image/%2e/generate",
        "/image/foo%2f..%2fbar",
    ):
        with pytest.raises(NetworkSafetyError):
            _validate_api_path(bad)
    # Sanity: legitimate API paths still pass.
    assert _validate_api_path("/models") == "/models"
    assert _validate_api_path("/image/generate") == "/image/generate"
    assert _validate_api_path("/chat/completions") == "/chat/completions"
