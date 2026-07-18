"""Small shared utilities and fail-closed media validators."""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from .errors import ContentValidationError, RequestValidationError

_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s\"']+"),
    re.compile(r"(?i)(venice_api_key\s*[:=]\s*)[^\s\"']+"),
    re.compile(r"\b(vapi_[A-Za-z0-9_-]{12,}|sk-[A-Za-z0-9_-]{12,})\b"),
)

# File-signature map used by the fail-closed validator. Each entry maps a
# declared MIME to a tuple of byte-range checks. The validator requires the
# *full* signature to be present before accepting any non-text declared
# type. Missing signatures or partial matches are rejected.
_PNG_SIG: Final[tuple[bytes, ...]] = (b"\x89PNG\r\n\x1a\n",)
_JPEG_SIG: Final[tuple[bytes, ...]] = (b"\xff\xd8\xff",)
_GIF_SIG: Final[tuple[bytes, ...]] = (b"GIF87a", b"GIF89a")
_MP3_SIG: Final[tuple[bytes, ...]] = (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xe0")
_FLAC_SIG: Final[tuple[bytes, ...]] = (b"fLaC",)
_OGG_SIG: Final[tuple[bytes, ...]] = (b"OggS",)
_OPUS_SIG: Final[tuple[bytes, ...]] = (b"OpusHead",)
_AAC_SIG: Final[tuple[bytes, ...]] = (b"\xff\xf1", b"\xff\xf9")
_WAV_RIFF_WINDOW: Final[tuple[int, int, bytes]] = (0, 12, b"RIFF\x00\x00\x00\x00WAVE")
_WEBP_RIFF_WINDOW: Final[tuple[int, int, bytes]] = (0, 12, b"RIFF\x00\x00\x00\x00WEBP")
_MP4_CHECK: Final[tuple[int, int, bytes]] = (4, 8, b"ftyp")
_AVI_CHECK: Final[tuple[int, int, bytes]] = (8, 12, b"AVI ")


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def timestamp_slug() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def stable_json(data: object) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def redact_text(value: str) -> str:
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(lambda match: f"{match.group(1) if match.lastindex else ''}[REDACTED]", redacted)
    return redacted


_REDACT_HEADERS = frozenset({"authorization", "api_key", "token", "venice_api_key", "x-auth-token"})


def redact_data(value: object) -> object:
    if isinstance(value, dict):
        output: dict[str, object] = {}
        for key, item in value.items():
            if key.lower() in _REDACT_HEADERS:
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
    """Encode a local file as a strictly-validated data URL."""
    path = Path(path_value).expanduser().resolve()
    if not path.is_file():
        raise RequestValidationError(f"Input file does not exist: {path}")
    size = path.stat().st_size
    if size > max_bytes:
        raise RequestValidationError(
            f"Input file is {size} bytes, exceeding the bridge limit of {max_bytes} bytes: {path}"
        )
    mime_type = mimetypes.guess_type(path.name)[0]
    data = path.read_bytes()
    if not mime_type or mime_type == "application/octet-stream":
        mime_type = detected_content_type(data) or "application/octet-stream"
    if mime_type != "application/octet-stream":
        # Fail-closed: validate the file we are about to embed.
        fast_validate_content_type(data, mime_type)
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def normalize_media_input(value: str, *, max_bytes: int = 50 * 1024 * 1024) -> str:
    if value.startswith(("http://", "https://")):
        return value
    if value.startswith("data:"):
        # Validate decoded bytes; if declared MIME is suspect, reject.
        mime, blob = decode_data_url(value)
        fast_validate_content_type(blob, mime)
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
        blob = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise RequestValidationError("Malformed base64 data URL.") from exc
    return mime_type, blob


def extension_for_content_type(content_type: str) -> str:
    normalized = content_type.split(";", 1)[0].strip().lower()
    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/flac": ".flac",
        "audio/aac": ".aac",
        "audio/ogg": ".ogg",
        "audio/opus": ".opus",
        "audio/pcm": ".pcm",
        "video/mp4": ".mp4",
        "video/quicktime": ".mov",
        "text/plain": ".txt",
        "application/json": ".json",
    }
    return mapping.get(normalized, mimetypes.guess_extension(normalized) or ".bin")


# ---------------------------------------------------------------------------
# Fail-closed content validation
# ---------------------------------------------------------------------------


def detected_content_type(data: bytes) -> str | None:
    """Identify a content type from the leading bytes.

    Returns ``None`` only for unknown or trivially small buffers. This is a
    best-effort detector used by inference; the strict validator uses
    :func:`fast_validate_content_type` to enforce signatures.
    """
    if len(data) < 4:
        return None
    # Quick rejection for obvious executable/text types.
    if data.startswith(b"MZ") or data.startswith(b"\x7fELF"):
        return "application/x-binary"
    if data.startswith(b"PK\x03\x04"):
        return "application/zip"
    for sig in _PNG_SIG:
        if data.startswith(sig):
            return "image/png"
    if data[:3] in (b"\xff\xd8\xff",):
        return "image/jpeg"
    for sig in _GIF_SIG:
        if data.startswith(sig):
            return "image/gif"
    if _matches_window(data, *_WEBP_RIFF_WINDOW):
        return "image/webp"
    if _matches_window(data, *_WAV_RIFF_WINDOW):
        return "audio/wav"
    if data[:4] == b"fLaC":
        return "audio/flac"
    if data[:3] == b"ID3":
        return "audio/mpeg"
    for sig in _MP3_SIG:
        if data.startswith(sig):
            return "audio/mpeg"
    if data[:4] == b"OggS":
        return "audio/ogg"
    if data.startswith(b"OpusHead"):
        return "audio/opus"
    for sig in _AAC_SIG:
        if data.startswith(sig):
            return "audio/aac"
    if _matches_window(data, *_MP4_CHECK):
        return "video/mp4"
    return None


def fast_validate_content_type(data: bytes, declared: str) -> None:
    """Raise :class:`ContentValidationError` if ``data`` does not match ``declared``.

    Fail-closed: any declared type without an unambiguous signature is
    rejected by default. The narrow set of types accepted without a
    bytewise check is: ``application/json`` (string starts with ``{`` or
    ``[``) and ``text/plain`` (round-trippable UTF-8 with no embedded NUL
    bytes). All image, audio, and video types are signature-checked.
    """
    if declared is None:
        raise ContentValidationError(
            declared="",
            detected=None,
            reason="no declared content type",
        )
    normalized = declared.split(";", 1)[0].strip().lower()
    if not data:
        raise ContentValidationError(
            declared=normalized,
            detected=None,
            reason="empty body",
        )
    detected = detected_content_type(data)
    sha = sha256_hex(data)

    if normalized.startswith("image/"):
        if normalized == "image/png":
            if not data.startswith(_PNG_SIG[0]):
                raise ContentValidationError(
                    declared=normalized,
                    detected=detected,
                    sha256=sha,
                    reason="missing or corrupt PNG signature",
                )
            return
        if normalized in {"image/jpeg", "image/jpg"}:
            if not data.startswith(b"\xff\xd8\xff"):
                raise ContentValidationError(
                    declared=normalized,
                    detected=detected,
                    sha256=sha,
                    reason="missing JPEG SOI/APP marker",
                )
            return
        if normalized == "image/webp":
            if not _matches_window(data, *_WEBP_RIFF_WINDOW):
                raise ContentValidationError(
                    declared=normalized,
                    detected=detected,
                    sha256=sha,
                    reason="missing WEBP RIFF container",
                )
            return
        if normalized == "image/gif":
            if not (data.startswith(b"GIF87a") or data.startswith(b"GIF89a")):
                raise ContentValidationError(
                    declared=normalized,
                    detected=detected,
                    sha256=sha,
                    reason="missing GIF87a/GIF89a signature",
                )
            return
        raise ContentValidationError(
            declared=normalized,
            detected=detected,
            sha256=sha,
            reason="unsupported declared image type",
        )
    if normalized.startswith("video/"):
        if normalized == "video/mp4":
            if not _matches_window(data, *_MP4_CHECK):
                raise ContentValidationError(
                    declared=normalized,
                    detected=detected,
                    sha256=sha,
                    reason="missing MP4 ftyp box",
                )
            return
        if normalized == "video/quicktime":
            if not _matches_window(data, *_MP4_CHECK):
                raise ContentValidationError(
                    declared=normalized,
                    detected=detected,
                    sha256=sha,
                    reason="missing QuickTime ftyp box",
                )
            return
        raise ContentValidationError(
            declared=normalized,
            detected=detected,
            sha256=sha,
            reason="unsupported declared video type",
        )
    if normalized.startswith("audio/"):
        if normalized in {"audio/wav", "audio/x-wav"}:
            if not _matches_window(data, *_WAV_RIFF_WINDOW):
                raise ContentValidationError(
                    declared=normalized,
                    detected=detected,
                    sha256=sha,
                    reason="missing WAV RIFF container",
                )
            return
        if normalized in {"audio/mpeg", "audio/mp3"}:
            ok = data[:3] == b"ID3" or any(data.startswith(s) for s in _MP3_SIG)
            if not ok:
                raise ContentValidationError(
                    declared=normalized,
                    detected=detected,
                    sha256=sha,
                    reason="missing MP3 ID3/frame sync",
                )
            return
        if normalized == "audio/flac":
            if not data.startswith(b"fLaC"):
                raise ContentValidationError(
                    declared=normalized,
                    detected=detected,
                    sha256=sha,
                    reason="missing FLAC fLaC marker",
                )
            return
        if normalized == "audio/opus":
            if not data.startswith(b"OpusHead"):
                raise ContentValidationError(
                    declared=normalized,
                    detected=detected,
                    sha256=sha,
                    reason="missing OpusHead packet",
                )
            return
        if normalized == "audio/ogg":
            if not data.startswith(b"OggS"):
                raise ContentValidationError(
                    declared=normalized,
                    detected=detected,
                    sha256=sha,
                    reason="missing OggS capture pattern",
                )
            return
        if normalized == "audio/aac":
            if not any(data.startswith(sig) for sig in _AAC_SIG):
                raise ContentValidationError(
                    declared=normalized,
                    detected=detected,
                    sha256=sha,
                    reason="missing AAC ADTS sync",
                )
            return
        if normalized == "audio/pcm":
            # PCM has no signature. Require that the body have no embedded
            # scripting/JSON markers, and reject any obviously suspicious
            # content.
            if b"<" in data and (b"<script" in data or b"<?xml" in data):
                raise ContentValidationError(
                    declared=normalized,
                    detected=detected,
                    sha256=sha,
                    reason="pcm body contains HTML/XML markers",
                )
            return
        raise ContentValidationError(
            declared=normalized,
            detected=detected,
            sha256=sha,
            reason="unsupported declared audio type",
        )
    if normalized == "application/json":
        stripped = data.lstrip()
        if not stripped or stripped[:1] not in (b"{", b"["):
            raise ContentValidationError(
                declared=normalized,
                detected=detected,
                sha256=sha,
                reason="application/json body is not an object or array",
            )
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContentValidationError(
                declared=normalized,
                detected=detected,
                sha256=sha,
                reason="application/json body is not valid UTF-8",
            ) from exc
        return
    if normalized == "text/plain":
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContentValidationError(
                declared=normalized,
                detected=detected,
                sha256=sha,
                reason="text/plain body is not valid UTF-8",
            ) from exc
        if b"<script" in data.lower() or b"<?xml" in data.lower():
            raise ContentValidationError(
                declared=normalized,
                detected=detected,
                sha256=sha,
                reason="text/plain body contains scripting/XML markers",
            )
        return
    raise ContentValidationError(
        declared=normalized,
        detected=detected,
        sha256=sha,
        reason="declared content type is not in the fail-closed allowlist",
    )


def is_suspicious_content(content: bytes, content_type: str) -> bool:
    """Best-effort defense-in-depth check; never used as the sole gate."""
    if not content_type:
        return False
    normalized = content_type.split(";", 1)[0].strip().lower()
    if not normalized.startswith(("image/", "audio/", "video/")):
        return False
    try:
        text = content.decode("utf-8", errors="ignore").lower()
    except UnicodeDecodeError:
        return False
    return any(
        marker in text
        for marker in (
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
        )
    )


def _matches_window(data: bytes, start: int, end: int, expected: bytes) -> bool:
    if len(data) < end:
        return False
    return data[start:end] == expected


# Backward-compatible alias for callers that still expect a boolean answer.
def validate_content_type(content: bytes, declared_type: str) -> bool:
    try:
        fast_validate_content_type(content, declared_type)
    except ContentValidationError:
        return False
    return True
