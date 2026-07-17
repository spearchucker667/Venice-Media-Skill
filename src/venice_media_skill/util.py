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
    """Convert a file path to a data URL with content sniffing.
    
    VMS-016 FIX: Use content sniffing in addition to filename-based MIME type.
    """
    path = Path(path_value).expanduser().resolve()
    if not path.is_file():
        raise RequestValidationError(f"Input file does not exist: {path}")
    size = path.stat().st_size
    if size > max_bytes:
        raise RequestValidationError(
            f"Input file is {size} bytes, exceeding the bridge limit of {max_bytes} bytes: {path}"
        )
    
    # First try filename-based MIME type
    mime_type = mimetypes.guess_type(path.name)[0]
    
    # If filename guess failed or is generic, try content sniffing
    if not mime_type or mime_type == "application/octet-stream":
        # Read first few bytes for content sniffing
        with path.open("rb") as f:
            header = f.read(32)  # Read first 32 bytes for sniffing
        
        # Try to detect from content
        detected_type = content_type_for_magic_bytes(header)
        if detected_type:
            mime_type = detected_type
    
    if not mime_type:
        mime_type = "application/octet-stream"
    
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


# Magic bytes for supported media formats (VMS-008 FIX)
# Maps content type to list of magic byte signatures
_MAGIC_BYTES: dict[str, list[bytes]] = {
    "image/jpeg": [b"\xff\xd8\xff"],
    "image/png": [b"\x89PNG\r\n\x1a\n"],
    "image/webp": [b"RIFF", b"WEBP"],
    "image/gif": [b"GIF87a", b"GIF89a"],
    "audio/mpeg": [b"ID3", b"\xff\xfb", b"\xff\xf3"],  # MP3
    "audio/wav": [b"RIFF", b"WAVE"],
    "audio/x-wav": [b"RIFF", b"WAVE"],
    "audio/flac": [b"fLaC"],
    "audio/aac": [b"\xff\xf1"],
    "audio/opus": [b"OpusHead"],
    "audio/pcm": [],  # No standard magic bytes, skip validation
    "video/mp4": [b"\x00\x00\x00\x20ftyp"],  # ISO base media file format
    "application/json": [b"{", b"["],
}


def content_type_for_magic_bytes(data: bytes) -> str | None:
    """Detect content type from magic bytes.
    
    VMS-008 FIX: Verify actual content matches declared content type.
    Returns the detected content type or None if unknown.
    """
    if len(data) < 2:
        return None
    
    # Check for common signatures - ordered by likelihood and specificity
    # JPEG: starts with 0xFFD8 and second byte is 0xFF
    if data.startswith(b"\xff\xd8") and len(data) >= 3:
        # The third byte is often 0xFF for JPEG
        if data[2:3] == b"\xff":
            return "image/jpeg"
        # Some JPEGs might have different third byte, but start is enough
        return "image/jpeg"
    
    # PNG: always starts with this exact sequence
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    
    # RIFF-based formats (WEBP, WAV)
    if data.startswith(b"RIFF") and len(data) >= 12:
        # Check for WEBP form type at offset 8
        if data[8:12] == b"WEBP":
            return "image/webp"
        # Check for WAVE form type at offset 8
        if data[8:12] == b"WAVE":
            return "audio/wav"
    
    # GIF
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    
    # MP3 with ID3 tag
    if data.startswith(b"ID3"):
        return "audio/mpeg"
    
    # MP3 frame sync
    if data.startswith(b"\xff\xfb") or data.startswith(b"\xff\xf3"):
        return "audio/mpeg"
    
    # FLAC
    if data.startswith(b"fLaC"):
        return "audio/flac"
    
    # AAC
    if data.startswith(b"\xff\xf1"):
        return "audio/aac"
    
    # Opus
    if data.startswith(b"OpusHead"):
        return "audio/opus"
    
    # JSON or text
    if data.startswith(b"{") or data.startswith(b"["):
        return "application/json"
    
    # MP4 - check for 'ftyp' at offset 4
    if len(data) >= 8 and data[4:8] == b"ftyp":
        return "video/mp4"
    
    return None


def validate_content_type(content: bytes, declared_type: str) -> bool:
    """Validate that content bytes match the declared content type.
    
    VMS-008 FIX: Prevent saving malicious content with wrong extension.
    
    Args:
        content: The binary content to validate
        declared_type: The content type declared in headers
    
    Returns:
        True if content appears valid for the declared type
        False if there's a mismatch or content is suspicious
    """
    if not content:
        return False
    
    # Normalize the declared type
    normalized = declared_type.split(";", 1)[0].strip().lower()
    
    # Get expected magic bytes for this type
    expected_magics = _MAGIC_BYTES.get(normalized, [])
    
    # If we don't have magic bytes for this type, we can't validate
    # but we also shouldn't reject (some types like text/plain have no standard magic)
    if not expected_magics:
        return True
    
    # Check if content matches any expected magic
    for magic in expected_magics:
        if content.startswith(magic):
            return True
    
    # Additional check: detect actual type from content
    detected_type = content_type_for_magic_bytes(content)
    if detected_type and detected_type != normalized:
        # Content type mismatch - this could be malicious
        return False
    
    return True


def is_suspicious_content(content: bytes, content_type: str) -> bool:
    """Check if content appears to be malicious or mislabeled.
    
    VMS-008 FIX: Detect HTML/XML/JSON content disguised as media.
    
    Args:
        content: The binary content
        content_type: The declared content type
    
    Returns:
        True if content appears suspicious
    """
    normalized = content_type.split(";", 1)[0].strip().lower()
    
    # Check if declared as media but looks like text
    media_types = {"image", "audio", "video"}
    is_media = any(normalized.startswith(t) for t in media_types)
    
    if is_media:
        # Check for HTML/XML content
        try:
            text = content.decode("utf-8", errors="ignore")
        except UnicodeDecodeError:
            # Can't decode as UTF-8, probably binary data
            return False
        
        text_lower = text.lower()
        
        # Look for HTML/XML tags
        suspicious_patterns = [
            "<!doctype html",
            "<html",
            "<head",
            "<body",
            "<script",
            "<?xml",
            "<svg",
            "javascript:",
            "onerror=",
            "onclick=",
            "<meta",
            "<link",
            "<style",
        ]
        
        for pattern in suspicious_patterns:
            if pattern in text_lower:
                return True
        
        # Check for JSON content
        # JSON-like content declared as media is suspicious
        stripped = text.strip()
        if (stripped.startswith("{") and stripped.endswith("}")) or \
           (stripped.startswith("[") and stripped.endswith("]")):
            # Looks like JSON
            return True
    
    return False
