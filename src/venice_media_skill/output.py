"""Media artifact decoding and output persistence."""

from __future__ import annotations

import base64
import json
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .client import ApiResponse
from .errors import OutputError
from .util import decode_data_url, extension_for_content_type, timestamp_slug, utc_now_iso


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
            artifact_path.write_bytes(content)
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
                sidecar.write_text(
                    json.dumps(sidecar_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
                )
                artifact["metadata_path"] = str(sidecar.resolve())
            artifacts.append(artifact)
        return artifacts


def _extract_blobs(response: ApiResponse) -> list[tuple[str, bytes]]:
    if response.content is not None:
        return [(response.content_type.split(";", 1)[0], response.content)]
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
    return len(value) >= 4 and len(value) % 4 == 0


def _media_type_for_key(key: str) -> str:
    return {"audio": "audio/mpeg", "video": "video/mp4"}.get(key, "image/png")


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
    if filename:
        candidate = directory / filename
        if not candidate.suffix:
            candidate = candidate.with_suffix(extension)
        if total > 1:
            candidate = candidate.with_name(f"{candidate.stem}-{index}{candidate.suffix}")
    else:
        stem = operation.replace(".", "-") + "-" + timestamp_slug()
        if total > 1:
            stem += f"-{index}"
        candidate = directory / f"{stem}{extension}"
    if candidate.exists() and not overwrite:
        counter = 2
        while True:
            numbered = candidate.with_name(f"{candidate.stem}-{counter}{candidate.suffix}")
            if not numbered.exists():
                candidate = numbered
                break
            counter += 1
    return candidate
