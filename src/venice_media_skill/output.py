"""Media artifact decoding and output persistence."""

from __future__ import annotations

import base64
import errno
import json
import os
import re
import shutil
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
    detected_content_type,
    extension_for_content_type,
    fast_validate_content_type,
    timestamp_slug,
    utc_now_iso,
)

# Keys whose value, if a base64 string, is *semantically* a media artifact.
# MIME type is detected from the decoded magic bytes, not asserted from the
# key name — a JPEG returned under "b64_json" must be recognised as image/jpeg,
# not coerced to image/png.
_BASE64_MEDIA_KEYS: frozenset[str] = frozenset({"b64_json", "base64", "image", "audio", "video"})

# How many bytes to read from the start of a file-path blob to validate
# its declared ``Content-Type`` against the on-disk magic-byte signature.
# 64 bytes is enough for every common media magic sequence we accept;
# readers must not slurp multi-megabyte files into RAM.
BLOB_MAGIC_PREFIX_READ: int = 64


@dataclass(slots=True, frozen=True)
class _Blob:
    """A single media artifact pulled out of an :class:`ApiResponse`.

    Exactly one of ``content`` or ``file_path`` is populated:

    - ``content`` is set when the download client returned bytes in
      memory (``download_public_bytes`` or a JSON base64 body).
    - ``file_path`` is set when the download client streamed the bytes
      to a tmp file and atomically renamed into a caller-supplied
      destination (``download_public_file``).

    ``sha256`` and ``observed`` come from the request the client
    measured and are reused so we never rehash gigabytes of media on
    the consumer side.
    """

    content_type: str
    content: bytes | None = None
    file_path: Path | None = None
    sha256: str | None = None
    observed: int = 0


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
        # Preflight-reserve all artifact and sidecar paths before writing
        # any blob, so a concurrent caller cannot race between our path
        # resolution and content commit.  On any reservation failure we
        # clean up placeholders from earlier reserved paths and raise.
        entries: list[tuple[_Blob, Path, Path | None]] = []
        reserved: list[Path] = []
        try:
            for blob in blobs:
                artifact_path = _resolve_artifact_path(
                    directory,
                    operation=operation,
                    filename=filename,
                    index=entries.__len__() + 1,
                    total=len(blobs),
                    content_type=blob.content_type,
                    overwrite=overwrite,
                )
                sidecar = artifact_path.with_suffix(artifact_path.suffix + ".metadata.json") if write_metadata else None
                _reserve_path(artifact_path, overwrite=overwrite)
                reserved.append(artifact_path)
                if sidecar is not None:
                    _reserve_path(sidecar, overwrite=overwrite)
                    reserved.append(sidecar)
                entries.append((blob, artifact_path, sidecar))
        except Exception:
            for p in reserved:
                with suppress(FileNotFoundError):
                    p.unlink()
            raise
        # After preflight reservation the caller owns every path — the
        # leaf functions open with O_CREAT|O_EXCL (overwrite=False) only
        # when there was no preflight; pass overwrite=True so they skip
        # the reservation and use the placeholder already in place.
        leaf_overwrite = True
        for blob, artifact_path, sidecar in entries:
            if blob.file_path is not None:
                _commit_file_blob(blob, artifact_path, overwrite=leaf_overwrite)
                bytes_written = blob.observed
                sha256 = blob.sha256 or _sha256_of_file(artifact_path)
            else:
                _atomic_write_bytes(artifact_path, blob.content or b"", allow_overwrite=leaf_overwrite)
                bytes_written = len(blob.content or b"")
                sha256 = blob.sha256 or _sha256(blob.content or b"")
            artifact = {
                "path": str(artifact_path.resolve()),
                "content_type": blob.content_type,
                "bytes": bytes_written,
                "sha256": sha256,
            }
            if sidecar is not None:
                sidecar_payload = {
                    "schema_version": 1,
                    "created_at": utc_now_iso(),
                    "operation": operation,
                    "artifact": artifact,
                    **metadata,
                }
                _atomic_write_text(
                    sidecar,
                    json.dumps(sidecar_payload, indent=2, sort_keys=True) + "\n",
                    allow_overwrite=leaf_overwrite,
                )
                artifact["metadata_path"] = str(sidecar.resolve())
            artifacts.append(artifact)
        return artifacts


def _reserve_path(target: Path, *, overwrite: bool) -> None:
    """Atomically claim ``target`` via ``O_CREAT|O_EXCL``.

    The caller owns the zero-byte placeholder; a subsequent
    ``os.replace(temp, target)`` on a file the caller holds is safe.
    When ``overwrite=True`` the call is a no-op.
    """
    if overwrite:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError as exc:
        raise OutputError(f"Output already exists (set overwrite=true): {target}") from exc


def _atomic_write_bytes(target: Path, data: bytes, *, allow_overwrite: bool = False) -> Path:
    """Atomically write ``data`` to ``target`` via tmp+sibling+os.replace.

    If ``allow_overwrite=False`` and ``target`` exists, raise. Otherwise
    we unconditionally write to a sibling temp file and ``os.replace``
    into place so the file is either the complete bytes we wrote or absent
    — there is no ``remove(target) ; write(tmp) ; replace`` window in which
    a crash would lose the original.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    owned = False
    if not allow_overwrite:
        try:
            fd = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            owned = True
        except FileExistsError as exc:
            raise OutputError(f"Output already exists (set overwrite=true): {target}") from exc
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
        return target.resolve()
    except Exception:
        with suppress(FileNotFoundError):
            os.unlink(temp_path)
        if owned:
            with suppress(FileNotFoundError):
                os.unlink(str(target))
        raise


def _atomic_write_text(target: Path, data: str, *, allow_overwrite: bool = False) -> Path:
    """Atomically write ``data`` to ``target`` via tmp+sibling+os.replace.

    If ``allow_overwrite=False`` and ``target`` exists, raise. Otherwise
    overwrite-in-place atomically. We never ``os.remove(target)`` first
    — this guarantees the prior contents are recoverable if the new
    write fails before ``os.replace`` swaps files in.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    owned = False
    if not allow_overwrite:
        try:
            fd = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            owned = True
        except FileExistsError as exc:
            raise OutputError(f"Output already exists (set overwrite=true): {target}") from exc
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
        return target.resolve()
    except Exception:
        with suppress(FileNotFoundError):
            os.unlink(temp_path)
        if owned:
            with suppress(FileNotFoundError):
                os.unlink(str(target))
        raise


def _commit_file_blob(blob: _Blob, final_path: Path, *, overwrite: bool) -> None:
    """Atomically commit a file-path blob into ``final_path``.

    Validates the on-disk magic bytes via ``fast_validate_content_type``
    against ``blob.content_type``, then renames the source into the
    caller's final path with ``os.replace``. If the final destination
    already exists the caller must opt in via ``overwrite=True``.
    """
    if blob.file_path is None:
        raise OutputError("file_path blob has no file_path.")
    if not blob.file_path.exists():
        raise OutputError(f"download was incomplete: {blob.file_path} does not exist")
    if blob.observed <= 0:
        raise OutputError(f"download was empty: {blob.file_path}")
    _validate_blob_magic(blob)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    owned = False
    if not overwrite:
        try:
            fd = os.open(str(final_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            owned = True
        except FileExistsError as exc:
            raise OutputError(f"Output already exists (set overwrite=true): {final_path}") from exc
    source_resolved = blob.file_path.resolve()
    try:
        os.replace(source_resolved, final_path)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            if owned:
                with suppress(FileNotFoundError):
                    final_path.unlink()
            raise
        fd, temp_path = tempfile.mkstemp(dir=final_path.parent, prefix=f".{final_path.name}.", suffix=".tmp")
        try:
            with source_resolved.open("rb") as source, os.fdopen(fd, "wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
                target.flush()
                os.fsync(target.fileno())
            copied = Path(temp_path)
            if copied.stat().st_size != blob.observed:
                raise OutputError("cross-device artifact copy size mismatch")
            if blob.sha256 is not None and _sha256_of_file(copied) != blob.sha256:
                raise OutputError("cross-device artifact copy SHA-256 mismatch")
            os.replace(copied, final_path)
            source_resolved.unlink()
        except Exception:
            with suppress(FileNotFoundError):
                Path(temp_path).unlink()
            if owned:
                with suppress(FileNotFoundError):
                    final_path.unlink()
            raise


def _validate_blob_magic(blob: _Blob) -> None:
    """Re-validate the magic bytes against the declared ``Content-Type``.

    The download sink verified the type on the wire; we revalidate on
    disk because we want to surface a clean error if the on-disk file
    disagrees with the header before the caller moves it via rename.
    """
    if blob.file_path is None:
        return
    read_bytes = max(1, min(BLOB_MAGIC_PREFIX_READ, blob.observed))
    with blob.file_path.open("rb") as handle:
        prefix = handle.read(read_bytes)
    fast_validate_content_type(prefix, blob.content_type)


def _extract_blobs(response: ApiResponse) -> list[_Blob]:
    """Pull every decodable media artifact out of a Venice :class:`ApiResponse`."""
    if response.file_path is not None and response.observed > 0:
        content_type = response.content_type.split(";", 1)[0] or "application/octet-stream"
        return [
            _Blob(
                content_type=content_type,
                content=None,
                file_path=response.file_path,
                sha256=response.sha256,
                observed=response.observed,
            )
        ]
    if response.content is not None:
        content_type = response.content_type.split(";", 1)[0] or "application/octet-stream"
        fast_validate_content_type(response.content, content_type)
        return [
            _Blob(
                content_type=content_type,
                content=response.content,
                sha256=response.sha256,
                observed=len(response.content),
            )
        ]
    return _extract_json_blobs(response.json_data)


def _extract_json_blobs(payload: Any) -> list[_Blob]:
    results: list[_Blob] = []
    if isinstance(payload, str):
        if payload.startswith("data:"):
            mime, blob = decode_data_url(payload)
            fast_validate_content_type(blob, mime)
            results.append(_Blob(content_type=mime, content=blob, observed=len(blob)))
        return results
    if isinstance(payload, list):
        for item in payload:
            results.extend(_extract_json_blobs(item))
        return results
    if not isinstance(payload, dict):
        return results
    for key, value in payload.items():
        if key in _BASE64_MEDIA_KEYS and isinstance(value, str):
            if value.startswith("data:"):
                mime, blob = decode_data_url(value)
                fast_validate_content_type(blob, mime)
                results.append(
                    _Blob(
                        content_type=mime,
                        content=blob,
                        sha256=_sha256(blob),
                        observed=len(blob),
                    )
                )
                continue
            if _looks_like_base64(value):
                try:
                    blob = base64.b64decode(value, validate=True)
                except ValueError as exc:
                    raise ContentValidationError(
                        declared="",
                        detected=None,
                        reason="invalid base64 payload",
                    ) from exc
                detected = detected_content_type(blob)
                if detected is None:
                    raise ContentValidationError(
                        declared="",
                        detected=None,
                        reason=(
                            f"base64 payload under '{key}' did not match a known "
                            "media signature; refusing to assert a content type "
                            "from the key name"
                        ),
                    )
                fast_validate_content_type(blob, detected)
                results.append(
                    _Blob(
                        content_type=detected,
                        content=blob,
                        sha256=_sha256(blob),
                        observed=len(blob),
                    )
                )
        elif key == "url" and isinstance(value, str) and value.startswith("data:"):
            mime, blob = decode_data_url(value)
            fast_validate_content_type(blob, mime)
            results.append(
                _Blob(
                    content_type=mime,
                    content=blob,
                    sha256=_sha256(blob),
                    observed=len(blob),
                )
            )
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


def _sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _sha256_of_file(path: Path) -> str:
    import hashlib

    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


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
    if resolved.exists() and not overwrite:
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
    # Overwrite=True semantics: the writer validates magic bytes and
    # atomically replaces the destination.
    return resolved


# Backward-compatible name for the sidecar writer used by consent.py.
def atomic_write_text(target: Path, data: str, *, allow_overwrite: bool = True) -> Path:
    """Atomically (re)write a small JSON-ish file to ``target``.

    Never ``os.remove(target)`` first: the previous contents remain
    recoverable while we write to a sibling tmp + ``os.replace``.
    """
    return _atomic_write_text(target, data, allow_overwrite=allow_overwrite)


def _deprecated_validate_safe_filename(filename: str) -> None:  # pragma: no cover - re-export
    _validate_safe_filename(filename)


# Backward-compatible aliases for legacy tests.
def _choose_path(directory: Path, **kwargs: Any) -> Path:
    return _resolve_artifact_path(directory, **kwargs)
