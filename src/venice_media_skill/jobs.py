"""Durable local state for queued Venice jobs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import OutputError
from .util import redact_data, sha256_text, stable_json, utc_now_iso


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
    ) -> dict[str, Any]:
        record = {
            "schema_version": 1,
            "media_type": media_type,
            "model": model,
            "queue_id": queue_id,
            "status": "queued",
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
            "request_sha256": sha256_text(stable_json(request)),
            "request": redact_data(request),
            "artifact": None,
            "last_response": None,
        }
        self._write(queue_id, record)
        return record

    def update(self, queue_id: str, **changes: Any) -> dict[str, Any]:
        record = self.get(queue_id)
        sanitized: dict[str, Any] = {key: redact_data(value) for key, value in changes.items()}
        record.update(sanitized)
        record["updated_at"] = utc_now_iso()
        self._write(queue_id, record)
        return record

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
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(path)
