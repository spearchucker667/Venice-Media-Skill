"""Small shared utilities."""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import RequestValidationError

_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s\"']+"),
    re.compile(r"(?i)(venice_api_key\s*[:=]\s*)[^\s\"']+"),
    re.compile(r"\b(vapi_[A-Za-z0-9_-]{12,}|sk-[A-Za-z0-9_-]{12,})\b"),
)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def timestamp_slug() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def stable_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def redact_text(value: str) -> str:
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(
            lambda match: f"{match.group(1) if match.lastindex else ''}[REDACTED]", redacted
        )
    return redacted


def redact_data(value: Any) -> Any:
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            if key.lower() in {"authorization", "api_key", "token", "venice_api_key"}:
                output[key] = "[REDACTED]"
            else:
                output[key] = redact_data(item)
        return output
    if isinstance(value, list):
        return [redact_data(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def path_to_data_url(path_value: str | Path, *, max_bytes: int = 50 * 1024 * 1024) -> str:
    path = Path(path_value).expanduser().resolve()
    if not path.is_file():
        raise RequestValidationError(f"Input file does not exist: {path}")
    size = path.stat().st_size
    if size > max_bytes:
        raise RequestValidationError(
            f"Input file is {size} bytes, exceeding the bridge limit of {max_bytes} bytes: {path}"
        )
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def normalize_media_input(value: str, *, max_bytes: int = 50 * 1024 * 1024) -> str:
    if value.startswith(("http://", "https://", "data:")):
        return value
    return path_to_data_url(value, max_bytes=max_bytes)


def decode_data_url(value: str) -> tuple[str, bytes]:
    if not value.startswith("data:") or "," not in value:
        raise RequestValidationError("Expected a base64 data URL.")
    header, encoded = value.split(",", 1)
    if ";base64" not in header:
        raise RequestValidationError("Only base64 data URLs are supported.")
    mime_type = header[5:].split(";", 1)[0] or "application/octet-stream"
    try:
        return mime_type, base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise RequestValidationError("Malformed base64 data URL.") from exc


def extension_for_content_type(content_type: str) -> str:
    normalized = content_type.split(";", 1)[0].strip().lower()
    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "audio/mpeg": ".mp3",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/flac": ".flac",
        "audio/aac": ".aac",
        "audio/opus": ".opus",
        "audio/pcm": ".pcm",
        "video/mp4": ".mp4",
        "text/plain": ".txt",
        "application/json": ".json",
    }
    return mapping.get(normalized, mimetypes.guess_extension(normalized) or ".bin")
