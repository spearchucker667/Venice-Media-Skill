from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from venice_media_skill.client import VeniceClient
from venice_media_skill.errors import ContentValidationError, PayloadValidationError, RequestValidationError
from venice_media_skill.jobs import JobStore
from venice_media_skill.output import ArtifactWriter
from venice_media_skill.payloads import build_image_edit, build_image_upscale
from venice_media_skill.request import MediaRequest
from venice_media_skill.runner import MediaRunner

PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xfa\xff\xff?\x03\x00\x05\xfe\x02\xfe\xa3\x9a\xfa\x05"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)
JPEG = b"\xff\xd8\xff\xe0" + b"test-jpeg-payload" + b"\xff\xd9"


def _request(image: str, *, parameters: dict[str, Any] | None = None, dry_run: bool = False) -> MediaRequest:
    mapping: dict[str, Any] = {
        "operation": "image.upscale",
        "inputs": {"image": image},
        "parameters": parameters or {},
    }
    if dry_run:
        mapping["execution"] = {"dry_run": True}
    return MediaRequest.from_mapping(mapping)


@pytest.mark.parametrize(("suffix", "content"), [(".png", PNG), (".jpg", JPEG)])
def test_local_image_becomes_exact_raw_base64(tmp_path: Path, suffix: str, content: bytes) -> None:
    source = tmp_path / f"source{suffix}"
    source.write_bytes(content)
    payload = build_image_upscale(_request(str(source), parameters={"scale": 4, "creativity": 0.0})).payload
    encoded = payload["image"]
    assert isinstance(encoded, str)
    assert not encoded.startswith("data:")
    assert base64.b64decode(encoded, validate=True) == content
    assert payload == {"image": encoded, "scale": 4, "creativity": 0.0}


def test_upscale_strips_only_validated_data_url_prefix() -> None:
    encoded = base64.b64encode(PNG).decode("ascii")
    payload = build_image_upscale(_request(f"data:image/png;base64,{encoded}")).payload
    assert payload["image"] == encoded
    assert base64.b64decode(payload["image"], validate=True) == PNG


def test_upscale_preserves_valid_raw_base64() -> None:
    encoded = base64.b64encode(PNG + b"\x00" * 512).decode("ascii")
    assert build_image_upscale(_request(encoded)).payload["image"] == encoded


@pytest.mark.parametrize("image", ["", "%%%not-base64%%%", "data:image/png;base64,%%%"])
def test_upscale_rejects_empty_or_malformed_media(image: str) -> None:
    with pytest.raises((RequestValidationError, PayloadValidationError, ContentValidationError)):
        build_image_upscale(_request(image))


@pytest.mark.parametrize("parameters", [{"scale": 3}, {"creativity": -0.001}, {"creativity": 0.021}])
def test_upscale_rejects_invalid_controls(parameters: dict[str, Any]) -> None:
    with pytest.raises((ValueError, PayloadValidationError)):
        build_image_upscale(_request(base64.b64encode(PNG).decode("ascii"), parameters=parameters))


def test_upscale_dry_run_redacts_raw_base64(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(PNG)
    runner = MediaRunner(
        client=None,  # type: ignore[arg-type]
        writer=ArtifactWriter(tmp_path / "output"),
        jobs=JobStore(tmp_path / "jobs"),
    )
    result = runner.run(_request(str(source), dry_run=True))
    serialized = json.dumps(result)
    assert base64.b64encode(PNG).decode("ascii") not in serialized
    assert result["api_request"]["image"] == {
        "kind": "inline_media",
        "encoding": "raw_base64",
        "redacted": True,
    }


def test_image_edit_retains_data_url_contract() -> None:
    data_url = "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")
    request = MediaRequest.from_mapping(
        {
            "operation": "image.edit",
            "model": "edit-model",
            "prompt": "preserve composition",
            "inputs": {"image": data_url},
        }
    )
    assert build_image_edit(request).payload["image"] == data_url


def test_mock_transport_receives_exact_upscale_json_and_writes_artifact(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(PNG)
    expected = base64.b64encode(PNG).decode("ascii")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v1/image/upscale"
        assert json.loads(request.content) == {"image": expected, "scale": 2, "creativity": 0.0}
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    with VeniceClient(
        base_url="https://api.example.test/api/v1",
        api_key="test_venice_key_not_real",
        transport=httpx.MockTransport(handler),
        allow_noncanonical_endpoint=True,
    ) as client:
        runner = MediaRunner(
            client=client,
            writer=ArtifactWriter(tmp_path / "output"),
            jobs=JobStore(tmp_path / "jobs"),
        )
        result = runner.run(_request(str(source), parameters={"scale": 2, "creativity": 0.0}))

    artifact = result["artifacts"][0]
    assert Path(artifact["path"]).read_bytes() == PNG
    metadata = json.loads(Path(artifact["metadata_path"]).read_text(encoding="utf-8"))
    assert metadata["api_request"]["image"]["redacted"] is True
    assert expected not in json.dumps(metadata)
