"""Media artifact decoding and output persistence."""

from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .client import ApiResponse
from .errors import ContentValidationError, OutputError
from .util import (
    decode_data_url,
    extension_for_content_type,
    fast_validate_content_type,
    timestamp_slug,
    utc_now_iso,
)


@dataclass(slots=True)
class ArtifactWriter:
    default_output_dir: Path

    def save_response(
        self,
        response: ApiResponse,
        *,
        operation: str,
        output_dir: str | None,
        filename: str | None,
        overwrite: bool,
        write_metadata: bool,
        metadata: dict[str, Any],
    ) -> list[dict[str, Any]]:
        directory = Path(output_dir).expanduser() if output_dir else self.default_output_dir
        directory.mkdir(parents=True, exist_ok=True)
        blobs = _extract_blobs(response)
        if not blobs:
            raise OutputError("Venice response did not contain decodable media.")
        artifacts: list[dict[str, Any]] = []
        for index, (content_type, content) in enumerate(blobs, start=1):
            artifact_path = _resolve_artifact_path(
                directory,
                operation=operation,
                filename=filename,
                index=index,
                total=len(blobs),
                content_type=content_type,
                overwrite=overwrite,
            )
            _atomic_write_bytes(artifact_path, content)
            artifact = {
                "path": str(artifact_path.resolve()),
                "content_type": content_type,
                "bytes": len(content),
                "sha256": _sha256(content),
            }
            if write_metadata:
                sidecar = artifact_path.with_suffix(artifact_path.suffix + ".metadata.json")
                sidecar_payload = {
                    "schema_version": 1,
                    "created_at": utc_now_iso(),
                    "operation": operation,
                    "artifact": artifact,
                    **metadata,
                }
                # Atomic rename happens inside ``_atomic_write_text``. We
                # only record the sidecar for later metadata_paths, not
                # because we need to commit it again.
                _atomic_write_text(
                    sidecar,
                    json.dumps(sidecar_payload, indent=2, sort_keys=True) + "\n",
                )
                artifact["metadata_path"] = str(sidecar.resolve())
            artifacts.append(artifact)
        return artifacts


def _atomic_write_bytes(target: Path, data: bytes) -> Path:
    """Atomically write ``data`` to ``target``. Returns the temp path used."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise OutputError(f"Output already exists (set overwrite=true): {target}")
    fd, temp_path = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            written = 0
            view = memoryview(data)
            while written < len(view):
                chunk = handle.write(view[written:])
                if not chunk:
                    raise OSError("Failed to write output bytes; the underlying file returned 0.")
                written += chunk
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, str(target))
        return Path(temp_path)
    except Exception:
        with suppress(FileNotFoundError):
            os.unlink(temp_path)
        raise


def _atomic_write_text(target: Path, data: str) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise OutputError(f"Output already exists (set overwrite=true): {target}")
    fd, temp_path = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            encoded = data.encode("utf-8")
            written = 0
            view = memoryview(encoded)
            while written < len(view):
                chunk = handle.write(view[written:])
                if not chunk:
                    raise OSError("Failed to write text: 0 bytes returned by write().")
                written += chunk
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, str(target))
        return Path(temp_path)
    except Exception:
        with suppress(FileNotFoundError):
            os.unlink(temp_path)
        raise


def _extract_blobs(response: ApiResponse) -> list[tuple[str, bytes]]:
    if response.content is not None:
        content_type = response.content_type.split(";", 1)[0]
        fast_validate_content_type(response.content, content_type)
        return [(content_type, response.content)]
    return _extract_json_blobs(response.json_data)


def _extract_json_blobs(payload: Any) -> list[tuple[str, bytes]]:
    results: list[tuple[str, bytes]] = []
    if isinstance(payload, str):
        if payload.startswith("data:"):
            mime, blob = decode_data_url(payload)
            fast_validate_content_type(blob, mime)
            results.append((mime, blob))
        return results
    if isinstance(payload, list):
        for item in payload:
            results.extend(_extract_json_blobs(item))
        return results
    if not isinstance(payload, dict):
        return results
    for key, value in payload.items():
        if key in {"b64_json", "base64"} and isinstance(value, str):
            if _looks_like_base64(value):
                media_type = _media_type_for_key(key)
                try:
                    blob = base64.b64decode(value, validate=True)
                except ValueError as exc:
                    raise ContentValidationError(
                        declared=media_type,
                        detected=None,
                        reason="invalid base64 payload",
                    ) from exc
                fast_validate_content_type(blob, media_type)
                results.append((media_type, blob))
        elif key in {"image", "audio", "video"} and isinstance(value, str):
            if value.startswith("data:"):
                mime, blob = decode_data_url(value)
                fast_validate_content_type(blob, mime)
                results.append((mime, blob))
            elif _looks_like_base64(value):
                media_type = _media_type_for_key(key)
                try:
                    blob = base64.b64decode(value, validate=True)
                except ValueError as exc:
                    raise ContentValidationError(
                        declared=media_type,
                        detected=None,
                        reason="invalid base64 payload",
                    ) from exc
                fast_validate_content_type(blob, media_type)
                results.append((media_type, blob))
        elif key == "url" and isinstance(value, str) and value.startswith("data:"):
            mime, blob = decode_data_url(value)
            fast_validate_content_type(blob, mime)
            results.append((mime, blob))
        elif key in {"data", "images", "results", "output"}:
            results.extend(_extract_json_blobs(value))
    return results


def _looks_like_base64(value: str) -> bool:
    if len(value) < 4:
        return False
    if len(value) % 4 != 0:
        return False
    if not re.fullmatch(r"[A-Za-z0-9+/=]*", value):
        return False
    return bool(re.search(r"[A-Za-z0-9]", value))


def _media_type_for_key(key: str) -> str:
    return {"audio": "audio/mpeg", "video": "video/mp4"}.get(key, "image/png")


def _sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _validate_safe_filename(filename: str) -> None:
    if not filename:
        return
    if "\x00" in filename:
        raise OutputError("output.filename contains null bytes")
    if len(filename) >= 2 and filename[1] == ":":
        raise OutputError("output.filename must not contain drive letters")
    if filename.startswith("\\\\") or filename.startswith("//"):
        raise OutputError("output.filename must not contain UNC paths")
    if filename.startswith("/") or filename.startswith("\\"):
        raise OutputError("output.filename must be a relative path")
    if ".." in filename:
        raise OutputError("output.filename must not contain path traversal sequences")
    if "/" in filename or "\\" in filename:
        raise OutputError("output.filename must not contain path separators")


def _resolve_artifact_path(
    directory: Path,
    *,
    operation: str,
    filename: str | None,
    index: int,
    total: int,
    content_type: str,
    overwrite: bool,
) -> Path:
    extension = extension_for_content_type(content_type)
    if filename:
        _validate_safe_filename(filename)
        directory = directory.expanduser().resolve()
        candidate = directory / filename
        if candidate.suffix:
            user_ext = candidate.suffix.lower()
            expected_ext = extension.lower()
            compatible = {
                ".jpg": {".jpg", ".jpeg"},
                ".jpeg": {".jpg", ".jpeg"},
                ".png": {".png"},
                ".webp": {".webp"},
                ".mp3": {".mp3", ".mpeg"},
                ".wav": {".wav"},
                ".mp4": {".mp4"},
            }
            if user_ext not in compatible.get(expected_ext, {expected_ext}):
                candidate = candidate.with_suffix(extension)
        else:
            candidate = candidate.with_suffix(extension)
        if total > 1:
            candidate = candidate.with_name(f"{candidate.stem}-{index}{candidate.suffix}")
    else:
        stem = operation.replace(".", "-") + "-" + timestamp_slug()
        if total > 1:
            stem += f"-{index}"
        candidate = directory / f"{stem}{extension}"
    resolved = candidate.resolve()
    if not resolved.parent.samefile(directory):
        raise OutputError(f"output.filename resolves to {resolved} which is outside {directory}")
    if resolved.exists():
        if not overwrite:
            counter = 2
            max_attempts = 10
            while counter <= max_attempts:
                numbered = resolved.with_name(f"{resolved.stem}-{counter}{resolved.suffix}")
                if not numbered.exists():
                    resolved = numbered
                    break
                counter += 1
            else:
                unique_stem = f"{resolved.stem}-{uuid.uuid4().hex[:8]}"
                resolved = resolved.with_name(f"{unique_stem}{resolved.suffix}")
        else:
            # Caller asked to overwrite. Remove existing file so the atomic
            # ``_atomic_write_bytes`` precondition (file absent) holds on
            # both POSIX and Windows.
            os.remove(resolved)
    return resolved


# Backward-compatible name for the sidecar writer used by consent.py.
def atomic_write_text(target: Path, data: str) -> Path:
    if target.exists():
        # Allow consent and approval records to overwrite themselves.
        os.remove(target)
    return _atomic_write_text(target, data)


def _deprecated_validate_safe_filename(filename: str) -> None:  # pragma: no cover - re-export
    _validate_safe_filename(filename)


# Backward-compatible aliases for legacy tests.
def _choose_path(directory: Path, **kwargs: Any) -> Path:
    return _resolve_artifact_path(directory, **kwargs)
