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
from .errors import OutputError
from .util import (
    decode_data_url,
    extension_for_content_type,
    is_suspicious_content,
    timestamp_slug,
    utc_now_iso,
    validate_content_type,
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
            artifact_path = _choose_path(
                directory,
                operation=operation,
                filename=filename,
                index=index,
                total=len(blobs),
                content_type=content_type,
                overwrite=overwrite,
            )
            # VMS-013 FIX: Use atomic writes to prevent truncated files
            # Write to temp file first, then atomically rename
            temp_fd = None
            try:
                # Create temp file in same directory for atomic rename
                temp_fd, temp_path = tempfile.mkstemp(
                    dir=directory,
                    prefix=f".{artifact_path.name}.",
                    suffix=".tmp"
                )
                os.write(temp_fd, content)
                os.fsync(temp_fd)
                os.close(temp_fd)
                temp_fd = None
                
                # Atomic rename
                os.rename(temp_path, str(artifact_path))
            except Exception:
                # Clean up temp file on failure
                if temp_fd:
                    try:
                        os.close(temp_fd)
                    except Exception:
                        pass
                if os.path.exists(temp_path):
                    try:
                        os.unlink(temp_path)
                    except Exception:
                        pass
                raise
            
            artifact = {
                "path": str(artifact_path.resolve()),
                "content_type": content_type,
                "bytes": len(content),
            }
            if write_metadata:
                sidecar = artifact_path.with_suffix(artifact_path.suffix + ".json")
                sidecar_payload = {
                    "schema_version": 1,
                    "created_at": utc_now_iso(),
                    "operation": operation,
                    "artifact": artifact,
                    **metadata,
                }
                # VMS-013 FIX: Atomic write for sidecar too
                sidecar_temp_fd = None
                sidecar_temp_path = None
                try:
                    sidecar_temp_fd, sidecar_temp_path = tempfile.mkstemp(
                        dir=directory,
                        prefix=f".{sidecar.name}.",
                        suffix=".tmp"
                    )
                    content_str = json.dumps(sidecar_payload, indent=2, sort_keys=True) + "\n"
                    os.write(sidecar_temp_fd, content_str.encode("utf-8"))
                    os.fsync(sidecar_temp_fd)
                    os.close(sidecar_temp_fd)
                    sidecar_temp_fd = None
                    
                    # Atomic rename
                    os.rename(sidecar_temp_path, str(sidecar))
                except Exception:
                    if sidecar_temp_fd:
                        try:
                            os.close(sidecar_temp_fd)
                        except Exception:
                            pass
                    if sidecar_temp_path and os.path.exists(sidecar_temp_path):
                        try:
                            os.unlink(sidecar_temp_path)
                        except Exception:
                            pass
                    raise
                artifact["metadata_path"] = str(sidecar.resolve())
            artifacts.append(artifact)
        return artifacts


def _extract_blobs(response: ApiResponse) -> list[tuple[str, bytes]]:
    if response.content is not None:
        content_type = response.content_type.split(";", 1)[0]
        content = response.content
        
        # VMS-008 FIX: Validate content type matches actual content
        if not validate_content_type(content, content_type):
            raise OutputError(
                f"Content type mismatch: declared as {content_type} but content "
                f"does not match expected format. Possible malicious response."
            )
        
        # VMS-008 FIX: Check for suspicious content (e.g., HTML disguised as image)
        if is_suspicious_content(content, content_type):
            raise OutputError(
                f"Suspicious content detected: declared as {content_type} but appears "
                f"to contain HTML/XML or other unexpected content. Rejecting."
            )
        
        return [(content_type, content)]
    return _extract_json_blobs(response.json_data)


def _extract_json_blobs(payload: Any) -> list[tuple[str, bytes]]:
    results: list[tuple[str, bytes]] = []
    if isinstance(payload, str):
        if payload.startswith("data:"):
            results.append(decode_data_url(payload))
        return results
    if isinstance(payload, list):
        for item in payload:
            results.extend(_extract_json_blobs(item))
        return results
    if not isinstance(payload, dict):
        return results
    for key, value in payload.items():
        if key in {"b64_json", "base64", "image", "audio", "video"} and isinstance(value, str):
            if value.startswith("data:"):
                results.append(decode_data_url(value))
            elif _looks_like_base64(value):
                media_type = _media_type_for_key(key)
                with suppress(ValueError):
                    results.append((media_type, base64.b64decode(value, validate=True)))
        elif key == "url" and isinstance(value, str) and value.startswith("data:"):
            results.append(decode_data_url(value))
        elif key in {"data", "images", "results", "output"}:
            results.extend(_extract_json_blobs(value))
    return results


def _looks_like_base64(value: str) -> bool:
    """Check if a string looks like base64-encoded data.
    
    VMS-015 FIX: More strict base64 detection to avoid misclassifying
    arbitrary strings. Requires:
    - Length >= 4 and divisible by 4 (base64 padding requirement)
    - Contains only valid base64 characters
    - Contains at least some alphanumeric characters
    """
    # Must have at least 4 characters (minimum meaningful base64)
    if len(value) < 4:
        return False
    
    # Must be divisible by 4 (base64 padding)
    if len(value) % 4 != 0:
        return False
    
    # Check that all characters are valid base64
    import re
    # Base64 alphabet: A-Z, a-z, 0-9, +, /, = (padding)
    if not re.fullmatch(r'[A-Za-z0-9+/=]*', value):
        return False
    
    # Must contain at least some alphanumeric characters
    # (strings with only +, /, = are probably not real base64)
    if not re.search(r'[A-Za-z0-9]', value):
        return False
    
    return True


def _media_type_for_key(key: str) -> str:
    return {"audio": "audio/mpeg", "video": "video/mp4"}.get(key, "image/png")


def _validate_safe_filename(filename: str) -> None:
    """Validate that a filename is safe and does not allow path traversal.
    
    Raises OutputError if the filename:
    - Is an absolute path (POSIX or Windows)
    - Contains path separators (/ or \\)
    - Contains path traversal sequences (..)
    - Contains null bytes
    - Contains Windows drive letters (C:, D:)
    - Contains UNC paths (server/share)
    - Is empty (but None is allowed)
    """
    if not filename:
        return
    
    # Check for null bytes
    if '\x00' in filename:
        raise OutputError("output.filename contains null bytes")
    
    # Check for Windows drive letters (C:, D:, etc.) at the start
    # Must check this before path separator check since drive letters contain :
    if len(filename) >= 2 and filename[1] == ':':
        raise OutputError("output.filename must not contain drive letters")
    
    # Check for UNC paths (\server/share or //server/share)
    # Must check before absolute path check since \\ starts with \
    if filename.startswith('\\\\') or filename.startswith('//'):
        raise OutputError("output.filename must not contain UNC paths")
    
    # Check for absolute paths (POSIX and Windows)
    if filename.startswith('/') or filename.startswith('\\'):
        raise OutputError("output.filename must be a relative path")
    
    # Check for path traversal patterns
    if '..' in filename:
        raise OutputError("output.filename must not contain path traversal sequences")
    
    # Check for path separators
    if '/' in filename or '\\' in filename:
        raise OutputError("output.filename must not contain path separators")


def _choose_path(
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
    
    # NEW: Validate filename safety before path construction
    if filename:
        _validate_safe_filename(filename)
    
    # Resolve directory to absolute path
    directory = directory.expanduser().resolve()
    
    if filename:
        candidate = directory / filename
        # VMS-012 FIX: Replace incompatible extension if user provided one
        # If filename has a suffix but it doesn't match the detected content type,
        # replace it with the correct extension
        if candidate.suffix:
            # Check if the user's extension is compatible with content type
            user_ext = candidate.suffix.lower()
            expected_ext = extension.lower()
            # Map of compatible extensions (e.g., .jpg and .jpeg are both JPEG)
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
                # Replace with correct extension
                candidate = candidate.with_suffix(extension)
        else:
            # No suffix provided, use the detected one
            candidate = candidate.with_suffix(extension)
        if total > 1:
            candidate = candidate.with_name(f"{candidate.stem}-{index}{candidate.suffix}")
    else:
        stem = operation.replace(".", "-") + "-" + timestamp_slug()
        if total > 1:
            stem += f"-{index}"
        candidate = directory / f"{stem}{extension}"
    
    # NEW: Resolve candidate and verify it stays within directory
    resolved_candidate = candidate.resolve()
    if not resolved_candidate.parent.samefile(directory):
        raise OutputError(
            f"output.filename resolves to {resolved_candidate} which is outside "
            f"the output directory {directory}"
        )
    
    # Use resolved candidate for existence checks
    candidate = resolved_candidate
    
    # VMS-014 FIX: Use UUID-based names to avoid race conditions
    # Check existence and handle collision, but use UUID for guaranteed uniqueness
    if candidate.exists() and not overwrite:
        # Generate a unique name using UUID to avoid race conditions
        # Still try numbered approach first for user-friendly names
        counter = 2
        max_attempts = 10  # Prevent infinite loop
        while counter <= max_attempts:
            numbered = candidate.with_name(f"{candidate.stem}-{counter}{candidate.suffix}")
            if not numbered.exists():
                candidate = numbered
                break
            counter += 1
        else:
            # Fall back to UUID-based name if all numbered attempts exist
            unique_stem = f"{candidate.stem}-{uuid.uuid4().hex[:8]}"
            candidate = candidate.with_name(f"{unique_stem}{candidate.suffix}")
    return candidate
