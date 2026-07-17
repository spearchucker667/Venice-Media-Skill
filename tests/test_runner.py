from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from venice_media_skill.client import ApiResponse
from venice_media_skill.errors import OutputError, RequestValidationError
from venice_media_skill.jobs import JobStore
from venice_media_skill.output import ArtifactWriter
from venice_media_skill.request import MediaRequest
from venice_media_skill.runner import MediaRunner


class FakeClient:
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
        return self.responses.pop(0)

    def download_public_url(self, url: str) -> ApiResponse:
        self.downloads.append(url)
        return self.responses.pop(0)


def make_runner(tmp_path: Path, client: FakeClient) -> MediaRunner:
    return MediaRunner(  # type: ignore[arg-type]
        client=client,
        writer=ArtifactWriter(tmp_path / "output"),
        jobs=JobStore(tmp_path / "jobs"),
    )


def request(mapping: dict[str, Any]) -> MediaRequest:
    return MediaRequest.from_mapping(mapping)


def test_dispatch_rejects_unknown_operation(tmp_path: Path) -> None:
    item = request({"operation": "image.generate", "model": "m", "prompt": "x"})
    item.operation = "unknown"
    with pytest.raises(RequestValidationError, match="Unsupported operation"):
        make_runner(tmp_path, FakeClient()).run(item)


def test_image_dry_run_injects_requested_defaults(tmp_path: Path) -> None:
    client = FakeClient()
    result = make_runner(tmp_path, client).run(
        request(
            {
                "operation": "image.generate",
                "model": "image-model",
                "prompt": "sunset",
                "parameters": {"width": 1024, "height": 1024},
                "execution": {"dry_run": True},
            }
        )
    )
    assert result["api_request"]["safe_mode"] is False
    assert result["api_request"]["hide_watermark"] is True
    assert result["api_request"]["return_binary"] is True
    assert client.calls == []


def test_image_generate_variants_saves_json_blobs(tmp_path: Path) -> None:
    encoded = base64.b64encode(b"png").decode()
    client = FakeClient(
        [ApiResponse(200, "application/json", {}, json_data={"data": [{"b64_json": encoded}]})]
    )
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


@pytest.mark.parametrize(
    ("operation", "endpoint", "inputs", "expected_key"),
    [
        ("image.edit", "/image/edit", {"images": ["https://x.test/a.png"]}, "image"),
        (
            "image.multi_edit",
            "/image/multi-edit",
            {"images": ["https://x.test/a.png", "https://x.test/b.png"]},
            "images",
        ),
        ("image.upscale", "/image/upscale", {"image": "https://x.test/a.png"}, "image"),
        (
            "image.background_remove",
            "/image/background-remove",
            {"image": "https://x.test/a.png"},
            "image_url",
        ),
    ],
)
def test_image_transform_dry_runs(
    tmp_path: Path,
    operation: str,
    endpoint: str,
    inputs: dict[str, Any],
    expected_key: str,
) -> None:
    mapping: dict[str, Any] = {
        "operation": operation,
        "inputs": inputs,
        "execution": {"dry_run": True},
    }
    if operation in {"image.edit", "image.multi_edit"}:
        mapping.update({"model": "edit-model", "prompt": "make it blue"})
    result = make_runner(tmp_path, FakeClient()).run(request(mapping))
    assert result["endpoint"] == endpoint
    assert expected_key in result["api_request"]
    if operation in {"image.edit", "image.multi_edit"}:
        assert result["api_request"]["safe_mode"] is False


def test_background_remove_local_file_uses_inline_image(tmp_path: Path) -> None:
    image = tmp_path / "source.png"
    image.write_bytes(b"png")
    result = make_runner(tmp_path, FakeClient()).run(
        request(
            {
                "operation": "image.background_remove",
                "inputs": {"image": str(image)},
                "execution": {"dry_run": True},
            }
        )
    )
    assert result["api_request"]["image"].startswith("data:image/png;base64,")


def test_image_edit_runtime_requires_string(tmp_path: Path) -> None:
    item = request(
        {
            "operation": "image.edit",
            "model": "m",
            "prompt": "x",
            "inputs": {"image": "https://x.test/a.png"},
        }
    )
    item.inputs["image"] = 3
    with pytest.raises(RequestValidationError, match="requires a string"):
        make_runner(tmp_path, FakeClient()).run(item)


def test_multi_edit_runtime_rejects_non_string(tmp_path: Path) -> None:
    item = request(
        {
            "operation": "image.multi_edit",
            "model": "m",
            "prompt": "x",
            "inputs": {"images": ["https://x.test/a.png"]},
        }
    )
    item.inputs["images"] = [1]
    with pytest.raises(RequestValidationError, match="string values"):
        make_runner(tmp_path, FakeClient()).run(item)


def test_tts_dry_run_and_save(tmp_path: Path) -> None:
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
    client = FakeClient([ApiResponse(200, "audio/mpeg", {}, content=b"mp3")])
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
    assert Path(result["artifacts"][0]["path"]).read_bytes() == b"mp3"
    assert client.calls[0]["json"]["input"] == "hello"


def test_transcribe_dry_run_and_json_response(tmp_path: Path) -> None:
    audio = tmp_path / "input.wav"
    audio.write_bytes(b"wav")
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

    client = FakeClient([ApiResponse(200, "application/json", {}, json_data={"text": "hello"})])
    result = make_runner(tmp_path, client).run(
        request(
            {
                "operation": "audio.transcribe",
                "model": "asr",
                "inputs": {"audio": str(audio)},
                "output": {"filename": "transcript.json"},
            }
        )
    )
    artifact = result["artifacts"][0]
    assert "hello" in Path(artifact["path"]).read_text()
    assert Path(artifact["metadata_path"]).is_file()
    assert client.calls[0]["files"] is not None


def test_transcribe_text_response_collision_and_empty(tmp_path: Path) -> None:
    audio = tmp_path / "input.wav"
    audio.write_bytes(b"wav")
    out = tmp_path / "out"
    out.mkdir()
    (out / "transcript.txt").write_text("old")
    client = FakeClient([ApiResponse(200, "text/plain", {}, content=b"new")])
    result = make_runner(tmp_path, client).run(
        request(
            {
                "operation": "audio.transcribe",
                "model": "asr",
                "inputs": {"audio": str(audio)},
                "output": {
                    "directory": str(out),
                    "filename": "transcript.txt",
                    "write_metadata": False,
                },
            }
        )
    )
    assert Path(result["artifacts"][0]["path"]).name != "transcript.txt"

    empty = FakeClient([ApiResponse(200, "application/json", {})])
    with pytest.raises(OutputError, match="empty"):
        make_runner(tmp_path, empty).run(
            request(
                {
                    "operation": "audio.transcribe",
                    "model": "asr",
                    "inputs": {"audio": str(audio)},
                }
            )
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


def test_video_quote_requires_approval_before_queue(tmp_path: Path) -> None:
    client = FakeClient([ApiResponse(200, "application/json", {}, json_data={"quote": 0.25})])
    result = make_runner(tmp_path, client).run(
        request(
            {
                "operation": "video.generate",
                "model": "video-model",
                "prompt": "sunset video",
                "parameters": {"duration": "5s"},
                "execution": {"quote_first": True, "confirmed_cost": False},
            }
        )
    )
    assert result["status"] == "approval_required"
    assert client.calls[0]["path"] == "/video/quote"
    assert len(client.calls) == 1


def test_audio_quote_then_queue_without_wait(tmp_path: Path) -> None:
    client = FakeClient(
        [
            ApiResponse(200, "application/json", {}, json_data={"quote": 0.1}),
            ApiResponse(200, "application/json", {}, json_data={"queue_id": "audio-1"}),
        ]
    )
    result = make_runner(tmp_path, client).run(
        request(
            {
                "operation": "audio.generate",
                "model": "music-model",
                "prompt": "ambient score",
                "parameters": {
                    "duration_seconds": 10,
                    "character_count": 13,
                    "ignored": "x",
                },
                "execution": {
                    "quote_first": True,
                    "confirmed_cost": True,
                    "wait": False,
                },
            }
        )
    )
    assert result["status"] == "queued"
    assert client.calls[0]["json"] == {
        "model": "music-model",
        "duration_seconds": 10,
        "character_count": 13,
    }


def test_queue_response_validation(tmp_path: Path) -> None:
    base = {
        "operation": "video.generate",
        "model": "m",
        "prompt": "x",
        "parameters": {"duration": "5s"},
    }
    with pytest.raises(OutputError, match="unexpected"):
        make_runner(
            tmp_path, FakeClient([ApiResponse(200, "application/json", {}, json_data=[])])
        ).run(request(base))
    with pytest.raises(OutputError, match="queue_id"):
        make_runner(
            tmp_path, FakeClient([ApiResponse(200, "application/json", {}, json_data={})])
        ).run(request(base))


def test_video_queue_polls_and_saves_binary_and_deletes_remote(tmp_path: Path) -> None:
    client = FakeClient(
        [
            ApiResponse(200, "application/json", {}, json_data={"queue_id": "queue-1"}),
            ApiResponse(200, "application/json", {}, json_data={"status": "PROCESSING"}),
            ApiResponse(200, "video/mp4", {}, content=b"video-bytes"),
            ApiResponse(200, "application/json", {}, json_data={"ok": True}),
        ]
    )
    result = make_runner(tmp_path, client).run(
        request(
            {
                "operation": "video.generate",
                "model": "video-model",
                "prompt": "sunset video",
                "parameters": {"duration": "5s"},
                "execution": {
                    "poll_interval_seconds": 0.001,
                    "timeout_seconds": 1,
                    "delete_remote_on_completion": True,
                },
                "output": {"directory": str(tmp_path / "artifacts")},
            }
        )
    )
    assert result["status"] == "completed"
    assert Path(result["artifacts"][0]["path"]).read_bytes() == b"video-bytes"
    record = JobStore(tmp_path / "jobs").get("queue-1")
    assert record["status"] == "completed"
    assert record["remote_media_deleted"] is True
    assert client.calls[-1]["path"] == "/video/complete"


def test_queue_download_url_is_persisted_and_used_for_retrieve(tmp_path: Path) -> None:
    client = FakeClient(
        [
            ApiResponse(
                200,
                "application/json",
                {},
                json_data={"queue_id": "q", "download_url": "https://cdn.test/v.mp4"},
            )
        ]
    )
    runner = make_runner(tmp_path, client)
    queued = runner.run(
        request(
            {
                "operation": "video.generate",
                "model": "m",
                "prompt": "x",
                "parameters": {"duration": "5s"},
                "execution": {"wait": False},
            }
        )
    )
    assert queued["download_url_present"] is True
    assert JobStore(tmp_path / "jobs").get("q")["download_url"] == "https://cdn.test/v.mp4"

    client.responses.extend(
        [
            ApiResponse(200, "application/json", {}, json_data={"status": "COMPLETED"}),
            ApiResponse(200, "video/mp4", {}, content=b"from-cdn"),
        ]
    )
    result = runner.run(
        request(
            {
                "operation": "video.retrieve",
                "model": "m",
                "parameters": {"queue_id": "q"},
            }
        )
    )
    assert client.downloads == ["https://cdn.test/v.mp4"]
    assert Path(result["artifacts"][0]["path"]).read_bytes() == b"from-cdn"


def test_retrieve_creates_missing_job_and_can_return_processing(tmp_path: Path) -> None:
    client = FakeClient(
        [ApiResponse(200, "application/json", {}, json_data={"status": "PROCESSING"})]
    )
    result = make_runner(tmp_path, client).run(
        request(
            {
                "operation": "audio.retrieve",
                "model": "m",
                "parameters": {"queue_id": "external"},
                "execution": {"wait": False},
            }
        )
    )
    assert result["status"] == "processing"
    assert JobStore(tmp_path / "jobs").get("external")["status"] == "processing"


@pytest.mark.parametrize("status", ["FAILED", "ERROR", "CANCELLED", "CANCELED"])
def test_retrieve_terminal_failure_status(tmp_path: Path, status: str) -> None:
    client = FakeClient([ApiResponse(200, "application/json", {}, json_data={"status": status})])
    result = make_runner(tmp_path, client).run(
        request(
            {
                "operation": "video.retrieve",
                "model": "m",
                "parameters": {"queue_id": "q"},
            }
        )
    )
    assert result["status"] == status.lower()


def test_retrieve_completed_without_media_raises(tmp_path: Path) -> None:
    client = FakeClient(
        [ApiResponse(200, "application/json", {}, json_data={"status": "COMPLETED"})]
    )
    with pytest.raises(OutputError, match="neither binary media"):
        make_runner(tmp_path, client).run(
            request(
                {
                    "operation": "video.retrieve",
                    "model": "m",
                    "parameters": {"queue_id": "q"},
                }
            )
        )


def test_retrieve_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    values = iter([0.0, 2.0])
    monkeypatch.setattr("venice_media_skill.runner.time.monotonic", lambda: next(values))
    client = FakeClient(
        [ApiResponse(200, "application/json", {}, json_data={"status": "PROCESSING"})]
    )
    result = make_runner(tmp_path, client).run(
        request(
            {
                "operation": "video.retrieve",
                "model": "m",
                "parameters": {"queue_id": "q"},
                "execution": {"timeout_seconds": 1, "poll_interval_seconds": 0.001},
            }
        )
    )
    assert result["status"] == "timed_out"


def test_video_queue_payload_maps_all_input_shapes(tmp_path: Path) -> None:
    image = tmp_path / "image.png"
    image.write_bytes(b"image")
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    result = make_runner(tmp_path, FakeClient()).run(
        request(
            {
                "operation": "video.generate",
                "model": "seedance-2-0-reference-to-video",
                "prompt": "Refer to <Subject 1> in <Image 1> to generate a clip.",
                "parameters": {"duration": "5s", "queue_id": "drop", "download_url": "drop"},
                "inputs": {
                    "image": str(image),
                    "end_image": "https://x.test/end.png",
                    "audio": "https://x.test/audio.mp3",
                    "video": str(video),
                    "reference_images": [str(image)],
                    "reference_videos": [str(video)],
                    "reference_audios": ["https://x.test/ref.mp3"],
                    "scene_images": [str(image)],
                    "elements": [{"name": "subject"}],
                },
                "attestations": {"seedance_face_consent": True},
                "execution": {"dry_run": True},
            }
        )
    )
    payload = result["api_request"]
    assert "queue_id" not in payload and "download_url" not in payload
    assert payload["image_url"].startswith("data:image/png;base64,")
    assert payload["video_url"].startswith("data:video/mp4;base64,")
    assert payload["reference_image_urls"][0].startswith("data:image/png;base64,")
    assert payload["elements"] == [{"name": "subject"}]
    # VMS-005: Consent is no longer automatically added. It must go through
    # the proper challenge-response flow. So we no longer expect consents here.
    assert "consents" not in payload


def test_video_quote_maps_video_input_and_allowlist(tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    client = FakeClient([ApiResponse(200, "application/json", {}, json_data={"price": 1})])
    result = make_runner(tmp_path, client).run(
        request(
            {
                "operation": "video.generate",
                "model": "m",
                "prompt": "x",
                "parameters": {
                    "duration": "5s",
                    "aspect_ratio": "16:9",
                    "resolution": "720p",
                    "ignored": "x",
                },
                "inputs": {"video": str(video)},
                "execution": {"quote_first": True},
            }
        )
    )
    quote = result["quote_request"]
    assert quote["duration"] == "5s"
    assert quote["video_url"].startswith("data:video/mp4;base64,")
    assert "ignored" not in quote
