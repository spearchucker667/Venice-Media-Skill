from __future__ import annotations

import base64
from pathlib import Path

import pytest

from venice_media_skill.client import ApiResponse
from venice_media_skill.errors import ContentValidationError
from venice_media_skill.output import ArtifactWriter

_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xfa\xff\xff?\x03\x00\x05\xfe\x02\xfe\xa3\x9a\xfa\x05"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)

# Minimal valid JPEG: SOI + APP0 (JFIF) + SOS header stub. Signature `\xff\xd8\xff`
# is sufficient for the fail-closed signature validator.
_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\xff\xd9"

# Minimal RIFF/WEBP container (RIFF + size + WEBP marker).
_WEBP = b"RIFF\x1c\x00\x00\x00WEBPVP8 \x0c\x00\x00\x00\x30\x01\x00\x9d\x01\x2a\x01\x00\xff\xff\xff\xff"


def test_binary_artifact_and_sidecar(tmp_path: Path) -> None:
    writer = ArtifactWriter(tmp_path)
    artifacts = writer.save_response(
        ApiResponse(200, "image/png", {}, content=_PNG),
        operation="image.generate",
        output_dir=None,
        filename="result.png",
        overwrite=False,
        write_metadata=True,
        metadata={"model": "m"},
    )
    assert Path(artifacts[0]["path"]).read_bytes() == _PNG
    assert Path(artifacts[0]["metadata_path"]).is_file()


def test_base64_json_artifact(tmp_path: Path) -> None:
    encoded = base64.b64encode(_PNG).decode()
    writer = ArtifactWriter(tmp_path)
    artifacts = writer.save_response(
        ApiResponse(200, "application/json", {}, json_data={"data": [{"b64_json": encoded}]}),
        operation="image.generate",
        output_dir=None,
        filename=None,
        overwrite=False,
        write_metadata=False,
        metadata={},
    )
    assert Path(artifacts[0]["path"]).read_bytes() == _PNG


# P2-05: b64_json MIME is detected from decoded magic bytes, never asserted
# from the JSON key name. This protects against Venice (or a future provider)
# returning a different encoding under the same well-known key.


def test_base64_json_jpeg_payload_is_detected_as_jpeg(tmp_path: Path) -> None:
    encoded = base64.b64encode(_JPEG).decode()
    writer = ArtifactWriter(tmp_path)
    artifacts = writer.save_response(
        ApiResponse(
            200,
            "application/json",
            {},
            json_data={"data": [{"b64_json": encoded}]},
        ),
        operation="image.generate",
        output_dir=None,
        filename=None,
        overwrite=False,
        write_metadata=False,
        metadata={},
    )
    saved = Path(artifacts[0]["path"])
    assert saved.read_bytes() == _JPEG
    # File extension follows the detected MIME, not the key name.
    assert saved.suffix == ".jpg"
    assert artifacts[0]["content_type"] == "image/jpeg"


def test_base64_json_webp_payload_is_detected_as_webp(tmp_path: Path) -> None:
    encoded = base64.b64encode(_WEBP).decode()
    writer = ArtifactWriter(tmp_path)
    artifacts = writer.save_response(
        ApiResponse(
            200,
            "application/json",
            {},
            json_data={"data": [{"b64_json": encoded}]},
        ),
        operation="image.generate",
        output_dir=None,
        filename=None,
        overwrite=False,
        write_metadata=False,
        metadata={},
    )
    assert Path(artifacts[0]["path"]).suffix == ".webp"
    assert artifacts[0]["content_type"] == "image/webp"


def test_base64_json_with_unknown_signature_is_rejected(tmp_path: Path) -> None:
    bogus = base64.b64encode(b"\x00\x01\x02\x03not-a-media-format-at-all\xff").decode()
    writer = ArtifactWriter(tmp_path)
    with pytest.raises(ContentValidationError) as excinfo:
        writer.save_response(
            ApiResponse(
                200,
                "application/json",
                {},
                json_data={"data": [{"b64_json": bogus}]},
            ),
            operation="image.generate",
            output_dir=None,
            filename=None,
            overwrite=False,
            write_metadata=False,
            metadata={},
        )
    assert "did not match a known media signature" in str(excinfo.value)
    assert "refusing to assert" in str(excinfo.value)


def test_non_media_json_keys_are_not_treated_as_media() -> None:
    """Closed-set base64 media key contract: only the allowlist extracts artifacts.

    Pins the P2-05 hardening: ``ArtifactWriter.save_response`` raises
    ``OutputError`` when no media blob is found, so we exercise the lower
    layer (``_extract_json_blobs``) to confirm non-media keys are ignored.
    """
    from venice_media_skill.output import _extract_json_blobs

    payload = {"logprobs": "-1.23", "finish_reason": "stop", "text": "hello"}
    assert _extract_json_blobs(payload) == []


def test_image_key_jpeg_payload_is_detected_as_jpeg(tmp_path: Path) -> None:
    """A semantically-declared ``image`` key carrying JPEG bytes gets image/jpeg.

    Prior to P2-05 the key ``image`` was coerced to ``image/png`` via name
    alone, rejecting valid JPEG payloads.
    """
    encoded = base64.b64encode(_JPEG).decode()
    writer = ArtifactWriter(tmp_path)
    artifacts = writer.save_response(
        ApiResponse(
            200,
            "application/json",
            {},
            json_data={"image": encoded},
        ),
        operation="image.generate",
        output_dir=None,
        filename=None,
        overwrite=False,
        write_metadata=False,
        metadata={},
    )
    saved = Path(artifacts[0]["path"])
    assert saved.read_bytes() == _JPEG
    assert saved.suffix == ".jpg"
    assert artifacts[0]["content_type"] == "image/jpeg"


def test_image_key_jpeg_via_extract(tmp_path: Path) -> None:
    """Pin the sym-key contract: image/audio/video keys are also sniffed from bytes."""
    from venice_media_skill.output import _extract_json_blobs

    encoded = base64.b64encode(_JPEG).decode()
    blobs = _extract_json_blobs({"image": encoded})
    assert len(blobs) == 1
    assert blobs[0].content_type == "image/jpeg"
    assert blobs[0].content == _JPEG


def test_b64_json_png_happy_path(tmp_path: Path) -> None:
    """Happy-path alias for the original b64_json + PNG contract."""
    encoded = base64.b64encode(_PNG).decode()
    writer = ArtifactWriter(tmp_path)
    artifacts = writer.save_response(
        ApiResponse(
            200,
            "application/json",
            {},
            json_data={"data": [{"b64_json": encoded}]},
        ),
        operation="image.generate",
        output_dir=None,
        filename=None,
        overwrite=False,
        write_metadata=False,
        metadata={},
    )
    assert Path(artifacts[0]["path"]).read_bytes() == _PNG
    assert artifacts[0]["content_type"] == "image/png"
