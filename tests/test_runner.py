from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from venice_media_skill.client import ApiResponse
from venice_media_skill.consent import ConsentStore, QuoteApprovalStore
from venice_media_skill.errors import (
    ConsentApprovalMissing,
    OutputError,
    PayloadValidationError,
    QuoteApprovalMismatch,
    RequestValidationError,
    ReservedParameterError,
)
from venice_media_skill.jobs import JobStore
from venice_media_skill.output import ArtifactWriter
from venice_media_skill.payloads import build_audio_queue, build_video_queue
from venice_media_skill.request import MediaRequest
from venice_media_skill.runner import MediaRunner

_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xfa\xff\xff?\x03\x00\x05\xfe\x02\xfe\xa3\x9a\xfa\x05"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


_MP4 = (
    b"\x00\x00\x00\x20ftypisom"  # ftyp box (size + type + brand)
    b"\x00\x00\x02\x00isomiso2avc1mp41"  # minor_version + compatible brands
    b"\x00\x00\x00\x08free" + b"\x00" * 8 + b"\x00\x00\x01\x00mdat" + b"\x00" * 32
)


class FakeClient:
    """Spy client with a FIFO response queue."""

    def __init__(self, responses: list[ApiResponse] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[dict[str, Any]] = []
        self.downloads: list[str] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> ApiResponse:
        self.calls.append(
            {
                "method": method,
                "path": path,
                "params": params,
                "json": json_body,
                "files": files,
                "data": data,
            }
        )
        if not self.responses:
            raise AssertionError("FakeClient exhausted its response queue.")
        return self.responses.pop(0)

    def download_public_url(self, url: str, **_: object) -> ApiResponse:
        self.downloads.append(url)
        if not self.responses:
            raise AssertionError("FakeClient exhausted its response queue.")
        return self.responses.pop(0)


def make_runner(
    tmp_path: Path,
    client: FakeClient,
    *,
    quote_store: QuoteApprovalStore | None = None,
    consent_store: ConsentStore | None = None,
) -> MediaRunner:
    return MediaRunner(  # type: ignore[arg-type]
        client=client,
        writer=ArtifactWriter(tmp_path / "output"),
        jobs=JobStore(tmp_path / "jobs"),
        consent_store=consent_store,
        quote_store=quote_store,
    )


def request(mapping: dict[str, Any]) -> MediaRequest:
    return MediaRequest.from_mapping(mapping)


def _png_bytes_response() -> ApiResponse:
    return ApiResponse(200, "image/png", {}, content=_PNG)


def _seed_job(tmp_path: Path, queue_id: str, *, media_type: str, model: str) -> None:
    """Pre-create a job record so ``*.<media_type>.retrieve`` can find it."""
    store = JobStore(tmp_path / "jobs")
    store.create(
        media_type=media_type,
        model=model,
        queue_id=queue_id,
        request={"operation": f"{media_type}.generate", "model": model, "prompt": "x"},
    )


def _seed_quote(
    tmp_path: Path,
    request_obj: MediaRequest,
    *,
    quote_response: dict[str, Any] | None = None,
) -> QuoteApprovalStore:
    """Pre-record a quote approval matching ``request_obj``'s canonical hash."""
    store = QuoteApprovalStore(tmp_path / "quote_approvals.json")
    if request_obj.operation.endswith("video.generate") or request_obj.operation == "video.generate":
        canonical = build_video_queue(request_obj)
    elif request_obj.operation == "audio.generate":
        canonical = build_audio_queue(request_obj)
    else:
        raise RuntimeError(f"unsupported operation for quote seeding: {request_obj.operation}")
    store.record(
        operation=canonical.operation,
        payload_hash=canonical.hash,
        quote_response=quote_response or {"quote": 1.0},
        max_cost=10.0,
    )
    return store


# ----- dispatch ------------------------------------------------------------


def test_dispatch_rejects_unknown_operation(tmp_path: Path) -> None:
    item = request({"operation": "image.generate", "model": "m", "prompt": "x"})
    item.operation = "unknown"
    with pytest.raises(RequestValidationError, match="Unsupported operation"):
        make_runner(tmp_path, FakeClient()).run(item)


# ----- image.generate ------------------------------------------------------


def test_image_dry_run_emits_canonical_image_generate_payload(tmp_path: Path) -> None:
    client = FakeClient()
    result = make_runner(tmp_path, client).run(
        request(
            {
                "operation": "image.generate",
                "model": "image-model",
                "prompt": "sunset",
                "execution": {"dry_run": True},
            }
        )
    )
    assert result["endpoint"] == "/image/generate"
    payload = result["api_request"]
    assert payload["model"] == "image-model"
    assert payload["prompt"] == "sunset"
    assert payload["safe_mode"] is False
    assert payload["hide_watermark"] is True
    assert payload["format"] == "webp"
    assert payload["return_binary"] is True
    assert client.calls == []  # no network call in dry_run


def test_image_generate_variants_2_disables_return_binary(tmp_path: Path) -> None:
    client = FakeClient([_png_bytes_response()])
    result = make_runner(tmp_path, client).run(
        request(
            {
                "operation": "image.generate",
                "model": "image-model",
                "prompt": "sunset",
                "parameters": {"variants": 2},
                "output": {"write_metadata": False},
            }
        )
    )
    assert result["status"] == "completed"
    assert client.calls[0]["json"]["return_binary"] is False


# ----- image transforms -------------------------------------------------------


@pytest.mark.parametrize(
    ("operation", "endpoint"),
    [
        ("image.edit", "/image/edit"),
        ("image.multi_edit", "/image/multi-edit"),
        ("image.upscale", "/image/upscale"),
        ("image.background_remove", "/image/background-remove"),
    ],
)
def test_image_transform_dry_runs(
    tmp_path: Path,
    operation: str,
    endpoint: str,
) -> None:
    mapping: dict[str, Any] = {
        "operation": operation,
        "execution": {"dry_run": True},
    }
    if operation in {"image.edit", "image.multi_edit"}:
        mapping.update({"model": "edit-model", "prompt": "make it blue"})
    if operation == "image.edit":
        mapping["inputs"] = {"image": _PNG_data_url()}
    if operation == "image.multi_edit":
        mapping["inputs"] = {"images": [_PNG_data_url()]}
    if operation in {"image.upscale", "image.background_remove"}:
        mapping["inputs"] = {"image": _PNG_data_url()}
    result = make_runner(tmp_path, FakeClient()).run(request(mapping))
    assert result["endpoint"] == endpoint


def _PNG_data_url() -> str:
    return "data:image/png;base64," + base64.b64encode(_PNG).decode()


def test_background_remove_local_file_is_loaded(tmp_path: Path) -> None:
    image = tmp_path / "source.png"
    image.write_bytes(_PNG)
    result = make_runner(tmp_path, FakeClient()).run(
        request(
            {
                "operation": "image.background_remove",
                "inputs": {"image": str(image)},
                "execution": {"dry_run": True},
            }
        )
    )
    # P1-10: dry-run redacts media bytes from the api_request payload; the
    # summary tells the host agent that a local file was supplied without
    # leaking the bytes themselves.
    payload = result["api_request"]
    assert payload["image"] == {
        "kind": "local_media",
        "mime_type": "image/png",
        "redacted": True,
    }
    summary = result["input_summary"]
    assert any(entry["name"] == "image" and entry["kind"] == "local_media" for entry in summary)


# ----- image.edit input shape -----------------------------------------------


def test_image_edit_input_must_be_string(tmp_path: Path) -> None:
    with pytest.raises((RequestValidationError, PayloadValidationError)):
        make_runner(tmp_path, FakeClient()).run(
            request(
                {
                    "operation": "image.edit",
                    "model": "m",
                    "prompt": "x",
                    "inputs": {"image": 3},
                }
            )
        )


def test_image_multi_edit_images_must_be_strings(tmp_path: Path) -> None:
    with pytest.raises((RequestValidationError, PayloadValidationError)):
        make_runner(tmp_path, FakeClient()).run(
            request(
                {
                    "operation": "image.multi_edit",
                    "model": "m",
                    "prompt": "x",
                    "inputs": {"images": [1]},
                }
            )
        )


# ----- audio.tts / transcribe ----------------------------------------------


def test_tts_dry_run_is_canonical(tmp_path: Path) -> None:
    dry = make_runner(tmp_path, FakeClient()).run(
        request(
            {
                "operation": "audio.tts",
                "model": "tts-model",
                "prompt": "hello",
                "parameters": {"voice": "voice"},
                "execution": {"dry_run": True},
            }
        )
    )
    assert dry["endpoint"] == "/audio/speech"
    payload = dry["api_request"]
    assert payload["model"] == "tts-model"
    assert payload["input"] == "hello"


def test_tts_save_writes_artifacts(tmp_path: Path) -> None:
    mp3_bytes = b"ID3" + b"\x00" * 32
    client = FakeClient([ApiResponse(200, "audio/mpeg", {}, content=mp3_bytes)])
    result = make_runner(tmp_path, client).run(
        request(
            {
                "operation": "audio.tts",
                "model": "tts-model",
                "prompt": "hello",
                "parameters": {"voice": "voice"},
            }
        )
    )
    artifact = result["artifacts"][0]
    assert Path(artifact["path"]).read_bytes().startswith(b"ID3")


def test_transcribe_dry_run_serializes_strict_bools(tmp_path: Path) -> None:
    audio = tmp_path / "input.wav"
    audio.write_bytes(_PNG)  # arbitrary bytes; mime validation not applied to audio path
    dry = make_runner(tmp_path, FakeClient()).run(
        request(
            {
                "operation": "audio.transcribe",
                "model": "asr",
                "inputs": {"audio": str(audio)},
                "parameters": {"language": "en", "timestamps": True},
                "execution": {"dry_run": True},
            }
        )
    )
    assert dry["multipart"]["timestamps"] == "true"
    assert dry["multipart"]["language"] == "en"


def test_transcribe_rejects_non_boolean_string_for_timestamps(tmp_path: Path) -> None:
    audio = tmp_path / "input.wav"
    audio.write_bytes(_PNG)
    with pytest.raises(PayloadValidationError):
        MediaRequest.from_mapping(
            {
                "operation": "audio.transcribe",
                "model": "asr",
                "inputs": {"audio": str(audio)},
                "parameters": {"timestamps": "false"},  # string "false" must NOT be accepted
            }
        )


def test_transcribe_rejects_missing_path_at_runtime(tmp_path: Path) -> None:
    with pytest.raises(RequestValidationError, match="does not exist"):
        make_runner(tmp_path, FakeClient()).run(
            request(
                {
                    "operation": "audio.transcribe",
                    "model": "asr",
                    "inputs": {"audio": str(tmp_path / "missing.wav")},
                }
            )
        )


def test_transcribe_response_with_empty_body_raises(tmp_path: Path) -> None:
    audio = tmp_path / "input.wav"
    audio.write_bytes(_PNG)
    bad = FakeClient([ApiResponse(200, "application/json", {}, json_data=[])])
    with pytest.raises(OutputError, match="empty"):
        make_runner(tmp_path, bad).run(
            request(
                {
                    "operation": "audio.transcribe",
                    "model": "asr",
                    "inputs": {"audio": str(audio)},
                }
            )
        )


# ----- quote approval -------------------------------------------------------


def test_video_quote_without_approval_surfaces_quote_approval_required(
    tmp_path: Path,
) -> None:
    client = FakeClient([ApiResponse(200, "application/json", {}, json_data={"quote": 0.25})])
    quote_store = QuoteApprovalStore(tmp_path / "quote_approvals.json")
    runner = make_runner(tmp_path, client, quote_store=quote_store)
    request_obj = request(
        {
            "operation": "video.generate",
            "model": "video-model",
            "prompt": "sunset video",
            "parameters": {"duration": "5s"},
            "execution": {"quote_first": True, "confirmed_cost": True},
        }
    )
    from venice_media_skill.errors import QuoteApprovalRequired

    with pytest.raises(QuoteApprovalRequired) as exc_info:
        runner.run(request_obj)
    assert "/video/quote" in client.calls[0]["path"]
    assert exc_info.value.quote == {"quote": 0.25}


def test_video_queue_with_quote_approval_proceeds(tmp_path: Path) -> None:
    """When a stored approval matches the canonical payload hash, the
    runner queues and never silently retries."""
    client = FakeClient(
        [
            ApiResponse(200, "application/json", {}, json_data={"quote": 0.1}),
            ApiResponse(200, "application/json", {}, json_data={"queue_id": "v1"}),
        ]
    )
    quote_store = QuoteApprovalStore(tmp_path / "quote_approvals.json")
    request_obj = request(
        {
            "operation": "video.generate",
            "model": "video-model",
            "prompt": "sunset video",
            "parameters": {"duration": "5s"},
            "execution": {"quote_first": True, "confirmed_cost": True, "wait": False},
        }
    )
    from venice_media_skill.payloads import build_video_queue

    canonical = build_video_queue(request_obj)
    quote_store.record(
        operation=request_obj.operation,
        payload_hash=canonical.hash,
        quote_response={"quote": 0.1},
        max_cost=0.5,
    )
    runner = make_runner(tmp_path, client, quote_store=quote_store)
    result = runner.run(request_obj)
    assert result["status"] == "queued"
    paths = [call["path"] for call in client.calls]
    assert "/" + "video/quote" in paths[0]
    # No silent retry of paid submission; quote + queue only.
    assert paths.count("/video/queue") == 1


def test_video_quote_max_cost_enforced(tmp_path: Path) -> None:
    client = FakeClient([ApiResponse(200, "application/json", {}, json_data={"quote": 9.9})])
    quote_store = QuoteApprovalStore(tmp_path / "quote_approvals.json")
    request_obj = request(
        {
            "operation": "video.generate",
            "model": "video-model",
            "prompt": "p",
            "parameters": {"duration": "5s"},
            "execution": {"quote_first": True, "confirmed_cost": True},
        }
    )
    from venice_media_skill.payloads import build_video_queue

    canonical = build_video_queue(request_obj)
    quote_store.record(
        operation=request_obj.operation,
        payload_hash=canonical.hash,
        quote_response={"quote": 0.5},
        max_cost=2.0,
    )
    runner = make_runner(tmp_path, client, quote_store=quote_store)
    with pytest.raises((QuoteApprovalMismatch, ConsentApprovalMissing, Exception)) as exc_info:
        runner.run(request_obj)
    # Any of these errors indicate the quote cap was rejected.
    assert (
        "max_cost" in str(exc_info.value).lower()
        or "approved" in str(exc_info.value).lower()
        or "mismatch" in str(exc_info.value).lower()
    )


# ----- queued retrieve ------------------------------------------------------


def test_queue_response_validation_missing_queue_id(tmp_path: Path) -> None:
    req = request(
        {
            "operation": "video.generate",
            "model": "m",
            "prompt": "x",
            "parameters": {"duration": "5s"},
            "execution": {"quote_first": False, "wait": False},
        }
    )
    with pytest.raises(OutputError, match="queue_id"):
        make_runner(
            tmp_path,
            FakeClient(
                [
                    ApiResponse(200, "application/json", {}, json_data={"quote": 1.0}),
                    ApiResponse(200, "application/json", {}, json_data={}),
                ]
            ),
            quote_store=_seed_quote(tmp_path, req),
        ).run(req)


def test_video_queue_polls_then_save_binary(tmp_path: Path) -> None:
    """Each call to ``runner.run`` creates a fresh ``MediaRunner`` so the
    client queue starts clean."""
    responses = [
        ApiResponse(200, "application/json", {}, json_data={"quote": 1.0}),
        ApiResponse(200, "application/json", {}, json_data={"queue_id": "queue-1"}),
        ApiResponse(200, "application/json", {}, json_data={"status": "PROCESSING"}),
        # The third call returns binary directly inside the polling loop.
        ApiResponse(200, "video/mp4", {}, content=b"\x00\x00\x00\x20ftypisom" + b"\x00" * 32),
    ]
    client = FakeClient(responses)
    request_obj = request(
        {
            "operation": "video.generate",
            "model": "video-model",
            "prompt": "sunset video",
            "parameters": {"duration": "5s"},
            "execution": {
                "quote_first": False,
                "wait": True,
                "poll_interval_seconds": 0.001,
                "timeout_seconds": 5,
                "delete_remote_on_completion": False,
            },
            "output": {"directory": str(tmp_path / "artifacts"), "write_metadata": False},
        }
    )
    runner = make_runner(tmp_path, client, quote_store=_seed_quote(tmp_path, request_obj))
    result = runner.run(request_obj)
    assert result["status"] == "completed"


def test_video_queue_pending_status_is_recorded(tmp_path: Path) -> None:
    responses = [
        ApiResponse(200, "application/json", {}, json_data={"quote": 1.0}),
        ApiResponse(200, "application/json", {}, json_data={"queue_id": "queue-1"}),
        ApiResponse(200, "application/json", {}, json_data={"status": "PROCESSING"}),
    ]
    client = FakeClient(responses)
    request_obj = request(
        {
            "operation": "video.generate",
            "model": "video-model",
            "prompt": "sunset video",
            "parameters": {"duration": "5s"},
            "execution": {"quote_first": False, "wait": False},
            "output": {"write_metadata": False},
        }
    )
    runner = make_runner(tmp_path, client, quote_store=_seed_quote(tmp_path, request_obj))
    queued = runner.run(request_obj)
    assert queued["status"] == "queued"
    assert JobStore(tmp_path / "jobs").get("queue-1")["status"] == "queued"

    # Without wait=False, polling would record processing.
    pollable_request = request(
        {
            "operation": "video.retrieve",
            "parameters": {"queue_id": "queue-1"},
            "execution": {
                "wait": False,
                "poll_interval_seconds": 0.001,
                "timeout_seconds": 1,
            },
        }
    )
    poll_client = FakeClient([ApiResponse(200, "application/json", {}, json_data={"status": "PROCESSING"})])
    poll_runner = make_runner(tmp_path, poll_client)
    poll_runner.run(pollable_request)
    assert JobStore(tmp_path / "jobs").get("queue-1")["status"] == "processing"


def test_retrieve_processing_returns_processing(tmp_path: Path) -> None:
    _seed_job(tmp_path, "external", media_type="audio", model="audio-model")
    client = FakeClient([ApiResponse(200, "application/json", {}, json_data={"status": "PROCESSING"})])
    runner = make_runner(tmp_path, client)
    result = runner.run(
        request(
            {
                "operation": "audio.retrieve",
                "parameters": {"queue_id": "external"},
                "execution": {"wait": False},
            }
        )
    )
    assert result["status"] == "processing"


@pytest.mark.parametrize("status", ["FAILED", "ERROR", "CANCELLED", "CANCELED"])
def test_retrieve_terminal_failure_status(tmp_path: Path, status: str) -> None:
    _seed_job(tmp_path, "q", media_type="video", model="video-model")
    client = FakeClient([ApiResponse(200, "application/json", {}, json_data={"status": status})])
    runner = make_runner(tmp_path, client)
    result = runner.run(
        request(
            {
                "operation": "video.retrieve",
                "parameters": {"queue_id": "q"},
            }
        )
    )
    assert result["status"] == status.lower()


def test_retrieve_completed_without_media_raises(tmp_path: Path) -> None:
    _seed_job(tmp_path, "q", media_type="video", model="video-model")
    client = FakeClient([ApiResponse(200, "application/json", {}, json_data={"status": "COMPLETED"})])
    with pytest.raises(OutputError, match="neither binary media"):
        make_runner(tmp_path, client).run(
            request(
                {
                    "operation": "video.retrieve",
                    "parameters": {"queue_id": "q"},
                }
            )
        )


def test_retrieve_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_job(tmp_path, "q", media_type="video", model="video-model")
    values = iter([0.0, 2.0])
    monkeypatch.setattr("venice_media_skill.runner.time.monotonic", lambda: next(values))
    client = FakeClient([ApiResponse(200, "application/json", {}, json_data={"status": "PROCESSING"})])
    result = make_runner(tmp_path, client).run(
        request(
            {
                "operation": "video.retrieve",
                "parameters": {"queue_id": "q"},
                "execution": {"timeout_seconds": 1, "poll_interval_seconds": 0.001},
            }
        )
    )
    assert result["status"] == "timed_out"


# ----- payload guards -------------------------------------------------------


def test_video_queue_payload_strips_reserved_keys(tmp_path: Path) -> None:
    """``parameters.download_url`` must NOT reach the queue body even if a
    caller crafts it. ``parameters.queue_id`` is *legitimate* on
    retrieve operations."""

    with pytest.raises(ReservedParameterError) as exc_info:
        MediaRequest.from_mapping(
            {
                "operation": "video.generate",
                "model": "video-model",
                "prompt": "p",
                "parameters": {"duration": "5s", "download_url": "http://attacker/x"},
            }
        )
    assert exc_info.value.key == "download_url"


def test_video_queue_payload_maps_inputs_to_provider_urls(tmp_path: Path) -> None:
    image = tmp_path / "image.png"
    image.write_bytes(_PNG)
    video = tmp_path / "video.mp4"
    # Write an actual MP4-shaped ftyp box so fail-closed validation accepts it.
    video.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00\x00\x02\x00isomiso2" + b"\x00" * 32)
    result = make_runner(tmp_path, FakeClient()).run(
        request(
            {
                "operation": "video.generate",
                "model": "seedance-2-0-reference-to-video",
                "prompt": "Refer to <Subject 1> in <Image 1> to generate a clip.",
                "parameters": {"duration": "5s"},
                "inputs": {
                    "image": str(image),
                    "end_image": _PNG_data_url(),
                    "audio": _MP4_data_url(),
                    "video": str(video),
                    "reference_images": [str(image)],
                    "reference_videos": [str(video)],
                    "reference_audios": [_MP4_data_url()],
                    "scene_images": [str(image)],
                    "elements": [{"name": "subject"}],
                },
                "execution": {"dry_run": True},
            }
        )
    )
    payload = result["api_request"]
    assert payload["image_url"]["kind"] == "local_media"
    assert payload["video_url"]["kind"] == "local_media"
    assert payload["reference_image_urls"][0]["kind"] == "local_media"
    assert payload["elements"] == [{"name": "subject"}]
    assert "consents" not in payload
    assert "queue_id" not in payload
    assert "download_url" not in payload


def _MP4_data_url() -> str:
    return "data:video/mp4;base64," + base64.b64encode(_MP4).decode()


def test_consent_block_attached_only_when_approval_matches(tmp_path: Path) -> None:
    """The runner attaches ``consents.seedance`` only when a stored
    approval matches the canonical payload hash."""
    store = ConsentStore(tmp_path / "consent_approvals.json")
    challenge = store.record_challenge(
        operation="video.generate",
        model="video-model",
        payload_hash="hash-x",
        input_hashes=(),
        provider_payload={"needs_consent": True, "consent_flow": "seedance"},
    )
    store.approve(
        challenge_id=challenge.challenge_id,
        confirmed_max_cost=None,
        acknowledge_policy=True,
    )
    # Build a payload that won't match the approval — runner must not
    # attach consents.
    client = FakeClient(
        [
            ApiResponse(200, "application/json", {}, json_data={"quote": 1.0}),
            ApiResponse(200, "application/json", {}, json_data={"queue_id": "v1"}),
        ]
    )
    request_obj = request(
        {
            "operation": "video.generate",
            "model": "video-model",
            "prompt": "p",
            "parameters": {"duration": "5s"},
            # Approval pre-seeded via quote_store below so consent gating is
            # testable in isolation.
            "execution": {"quote_first": False, "wait": False},
        }
    )
    runner = make_runner(
        tmp_path,
        client,
        consent_store=store,
        quote_store=_seed_quote(tmp_path, request_obj),
    )
    queued = runner.run(request_obj)
    assert queued["status"] == "queued"
    # The runner never auto-attached a consents block because the stored
    # approval's payload_hash is unrelated to the current request.
    queue_body = client.calls[-1]["json"]
    assert "consents" not in queue_body


def test_consent_block_absent_when_no_approval_stored(tmp_path: Path) -> None:
    """F-04-doc regression guard: when no consent approval is stored at all,
    the runner must never set ``consents.seedance`` on the queue body even if
    the model is a Seedance model with the face-consent attestation. The
    provider's 409 needs_consent response is the only signal that triggers
    re-submission, and that resend path is gated by ``runner.py:280``.

    A future refactor that drops the ``if consent_block is not None`` check
    would auto-resubmit a Seedance generation without explicit user
    consent — a critical safety violation. This test fails loudly in that
    regression case.
    """
    consent_store = ConsentStore(tmp_path / "consent_approvals.json")
    # Empty store: no challenge, no approval.

    client = FakeClient(
        [
            # Quote succeeds (cost is acknowledged by the runner).
            ApiResponse(200, "application/json", {}, json_data={"quote": 1.0}),
            # Queue call returns 409 needs_consent on the FIRST attempt.
            ApiResponse(
                409,
                "application/json",
                {},
                json_data={
                    "error": {"code": "needs_consent", "message": "required"},
                    "consent_flow": "seedance",
                    "consent": {"policy_text": "exact policy"},
                },
            ),
        ]
    )
    request_obj = request(
        {
            "operation": "video.generate",
            "model": "seedance-2-0-image-to-video",
            "prompt": "A person walks across the bridge.",
            "parameters": {"duration": "5s"},
            "inputs": {"image": _PNG_data_url_safe(tmp_path)},
            "execution": {"quote_first": False, "wait": False},
        }
    )
    runner = make_runner(
        tmp_path,
        client,
        consent_store=consent_store,
        quote_store=_seed_quote(tmp_path, request_obj),
    )
    result = runner.run(request_obj)
    # The runner must surface consent_required and capture the challenge.
    assert result["status"] == "consent_required"
    # The single on-wire queue call must NOT contain a consents block.
    queue_calls = [c for c in client.calls if c["path"].endswith("/video/queue")]
    assert queue_calls, "expected at least one POST /video/queue call"
    queue_body = queue_calls[-1]["json"]
    assert "consents" not in queue_body, (
        "Seedance face-consent was auto-attached on the first queue call "
        "without a stored user approval — this is a regression of the "
        "single-line gate at runner.py:280."
    )
    # A challenge must have been recorded for the host's review path.
    challenge_id = result["challenge_id"]
    assert consent_store.load_challenge(challenge_id) is not None


def _PNG_data_url_safe(tmp_path: Path) -> str:
    """Helper: write a valid PNG and return its file path so the runner
    accepts it as a local media input (no re-encoding to data: URLs).
    """
    png_path = tmp_path / "image.png"
    png_path.write_bytes(_PNG)
    return str(png_path)
