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
    source.write_bytes(b"png-data")
    data_url = path_to_data_url(source)
    mime, content = decode_data_url(data_url)
    assert mime == "image/png"
    assert content == b"png-data"


def test_normalize_preserves_urls() -> None:
    assert (
        normalize_media_input("https://example.test/image.png") == "https://example.test/image.png"
    )


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
