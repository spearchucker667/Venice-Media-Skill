from __future__ import annotations

from pathlib import Path

import pytest

from venice_media_skill.errors import RequestValidationError
from venice_media_skill.util import (
    decode_data_url,
    normalize_media_input,
    path_to_data_url,
    redact_data,
)


def test_path_to_data_url_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "sample.png"
    source.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\rIDATx\x9cc\xfa\xff\xff?\x03\x00\x05\xfe\x02\xfe\xa3\x9a\xfa\x05"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    data_url = path_to_data_url(source)
    mime, content = decode_data_url(data_url)
    assert mime == "image/png"
    assert content.startswith(b"\x89PNG\r\n\x1a\n")


def test_normalize_preserves_urls() -> None:
    assert normalize_media_input("https://example.test/image.png") == "https://example.test/image.png"


def test_missing_file_rejected() -> None:
    with pytest.raises(RequestValidationError, match="does not exist"):
        path_to_data_url("/definitely/missing/file.png")


def test_redaction_removes_credentials() -> None:
    payload = {
        "authorization": "Bearer secret",
        "nested": "VENICE_API_KEY=vapi_abcdefghijklmnopqrstuvwxyz",
    }
    redacted = redact_data(payload)
    assert redacted["authorization"] == "[REDACTED]"
    assert "abcdefghijklmnopqrstuvwxyz" not in redacted["nested"]
