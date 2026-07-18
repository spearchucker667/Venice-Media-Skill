"""Durable local state for queued Venice jobs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .consent import _acquire_lock, _release_lock
from .errors import OutputError
from .output import atomic_write_text
from .util import sha256_text, stable_json, utc_now_iso

_QUERY_REDACT: re.Pattern[str] = re.compile(
    r"((?:token|key|secret|signature|sig|api_key|access_token)=)[^&]+", re.IGNORECASE
)
_RECORD_SIZE_LIMIT: int = 256 * 1024  # 256 KiB cap per job record


def _redact_url_query(url: str) -> str:
    return _QUERY_REDACT.sub(r"\1[REDACTED]", url)


def _sanitize_record_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _sanitize_record_payload(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_record_payload(v) for v in value]
    if isinstance(value, str):
        if value.startswith(("http://", "https://")):
            return _redact_url_query(value)
        if value.startswith("data:"):
            return f"data:{value.split(',')[0]},[REDACTED:{len(value)}bytes]"
        return value
    return value


def _sanitize_download_url(value: Any) -> Any:
    if isinstance(value, str):
        if value.startswith(("http://", "https://")):
            return _redact_url_query(value)
        if value.startswith("data:"):
            header = value.split(",", 1)[0]
            return f"{header},[REDACTED:{len(value)}bytes]"
    return value


@dataclass(slots=True)
class JobStore:
    root: Path

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def create(
        self,
        *,
        media_type: str,
        model: str,
        queue_id: str,
        request: dict[str, Any],
        input_summary: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        record = {
            "schema_version": 2,
            "media_type": media_type,
            "model": model,
            "queue_id": queue_id,
            "status": "queued",
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
            "request_sha256": sha256_text(stable_json(request)),
            "input_summary": input_summary,
            "artifact": None,
            "last_response": None,
        }
        self._write(queue_id, record)
        return record

    def update(self, queue_id: str, **changes: Any) -> dict[str, Any]:
        path = self._path(queue_id)
        _acquire_lock(path)
        try:
            record = self.get(queue_id)
            sanitized: dict[str, Any] = {}
            for key, value in changes.items():
                if key == "download_url":
                    sanitized[key] = _sanitize_download_url(value)
                else:
                    sanitized[key] = _sanitize_record_payload(value)
            record.update(sanitized)
            record["updated_at"] = utc_now_iso()
            self._write(queue_id, record)
            return record
        finally:
            _release_lock(path)

    def get(self, queue_id: str) -> dict[str, Any]:
        path = self._path(queue_id)
        if not path.is_file():
            raise OutputError(f"No local job record exists for queue_id={queue_id}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise OutputError(f"Unable to read job record {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise OutputError(f"Job record {path} is not a JSON object.")
        return payload

    def list(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*.json"), reverse=True):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                records.append(payload)
        return records

    def _path(self, queue_id: str) -> Path:
        safe_id = "".join(char for char in queue_id if char.isalnum() or char in {"-", "_"})
        if not safe_id or safe_id != queue_id:
            raise OutputError("queue_id contains unsupported characters.")
        return self.root / f"{safe_id}.json"

    def _write(self, queue_id: str, record: dict[str, Any]) -> None:
        path = self._path(queue_id)
        text = json.dumps(record, indent=2, sort_keys=True) + "\n"
        if len(text.encode("utf-8")) > _RECORD_SIZE_LIMIT:
            raise OutputError(
                f"Job record for {queue_id} exceeds {_RECORD_SIZE_LIMIT}-byte cap; "
                "reduce input size or avoid inline media."
            )
        atomic_write_text(path, text)
