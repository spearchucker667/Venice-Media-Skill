"""Durable local state for queued Venice jobs."""

from __future__ import annotations

import json
import os
import re
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .consent import _acquire_lock, _release_lock
from .errors import OutputError
from .output import atomic_write_text
from .util import sha256_text, stable_json, utc_now_iso

_QUERY_REDACT: re.Pattern[str] = re.compile(
    r"((?:token|key|secret|signature|sig|api_key|access_token|keyid|expires)=)[^&]+",
    re.IGNORECASE,
)
_RECORD_SIZE_LIMIT: int = 256 * 1024  # 256 KiB cap per job record

# Statuses a job record can carry. ``pending_finalize`` is the durable
# recovery marker written *before* quote/consent approvals are finalized;
# once finalisation succeeds the runner promotes the record to
# ``queued``. ``completed_without_media`` is reserved for Venice
# completions that returned neither binary data nor a download URL.
_ALLOWED_STATUSES: frozenset[str] = frozenset(
    {
        "pending_finalize",
        "queued",
        "processing",
        "completed",
        "completed_without_media",
        "failed",
        "error",
        "cancelled",
        "canceled",
        "timed_out",
    }
)

# Job-record schema version. v3 introduces the download-URL split: the
# signed URL is stored in a sidecar file (``download_url_secret_ref``)
# while only the redacted display copy is kept in the record itself
# (``download_url_display``). v2 records are upgraded on read by
# :meth:`get` and by :meth:`update` so callers always see the v3 shape.
SCHEMA_VERSION: int = 3

# Relative directory under ``JobStore.root`` that holds download-URL
# sidecar files. The directory is created on demand inside the locked
# section of :meth:`update`.
_DOWNLOAD_SECRET_DIRNAME: str = "download_secrets"

# Fields that are always redacted before being written to disk. The
# runner is responsible for other persisted state staying free of
# secrets (Authorization headers, raw tokens, etc.) so this catch-all
# mainly catches HTTP URLs leaking through ``last_response`` or
# ``input_summary``.
_DOWNLOAD_DISPLAY_FIELDS: frozenset[str] = frozenset({"download_url_display", "download_url"})


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


def _sanitize_legacy_download_url(value: Any) -> Any:
    """Backwards-compatible redaction used by v2 record migration.

    v2 stored the redacted URL directly in the ``download_url`` field,
    and we still encounter those records on read. Strip the redacted
    copy and surface it as the display value with no sidecar.
    """
    if isinstance(value, str):
        if value.startswith(("http://", "https://")):
            return _redact_url_query(value)
        if value.startswith("data:"):
            header = value.split(",", 1)[0]
            return f"{header},[REDACTED:{len(value)}bytes]"
    return value


def _sanitize_download_url_for_display(url: str) -> str:
    """Return a redacted ``display`` copy of ``url``.

    The display copy is safe to log or hand to the operator UI; it
    preserves the host/path while blanking signature-bearing query
    parameters so an attacker scraping logs cannot reuse the signed
    URL even if the record is reachable.
    """
    if url.startswith(("http://", "https://")):
        return _redact_url_query(url)
    if url.startswith("data:"):
        header = url.split(",", 1)[0]
        return f"{header},[REDACTED:{len(url)}bytes]"
    return url


@dataclass(slots=True)
class JobStore:
    root: Path

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def _secret_dir(self) -> Path:
        path = self.root / _DOWNLOAD_SECRET_DIRNAME
        path.mkdir(parents=False, exist_ok=True)
        with suppress(OSError):
            os.chmod(path, 0o700)
        return path

    def _secret_path(self, queue_id: str) -> Path:
        return self._secret_dir() / f"{queue_id}.url"

    def download_url_for(self, queue_id: str) -> str | None:
        """Return the live signed download URL for ``queue_id``.

        Reads the sidecar file written by :meth:`update` so the runner
        can hand a real URL to the public downloader. Returns ``None``
        when no sidecar exists (e.g. completion returned binary media
        inline, the call returned without ever having a URL, or the
        record is the v2 migrated form that has no secret).
        """
        path = self._path(queue_id)
        _acquire_lock(path)
        try:
            record = self._load_raw(queue_id)
            ref = record.get("download_url_secret_ref") if isinstance(record, dict) else None
            if not isinstance(ref, str) or not ref:
                return None
            secret_path = Path(ref)
            if not secret_path.is_file():
                return None
            try:
                value = secret_path.read_text(encoding="utf-8")
            except OSError:
                return None
            value = value.strip()
            return value or None
        finally:
            _release_lock(path)

    def create(
        self,
        *,
        media_type: str,
        model: str,
        queue_id: str,
        request: dict[str, Any],
        input_summary: list[dict[str, Any]] | None = None,
        status: str = "queued",
    ) -> dict[str, Any]:
        """Persist a brand-new durable record for ``queue_id``.

        ``status`` defaults to ``"queued"`` but callers performing the
        queue-commit three-phase pattern (claim → durable write →
        finalize approvals → promote to ``queued``) pass
        ``status="pending_finalize"`` to durably record the
        provider-accepted ``queue_id`` **before** consuming the quote
        and/or consent approvals.

        A ``"pending_finalize"`` record is the durable recovery marker:
        if the runner crashes between writing the record and finalizing
        the approvals, the next ``venice-media`` invocation surfaces the
        record through :meth:`list` so the operator can resume by
        running ``video.retrieve`` / ``audio.retrieve`` with that
        ``queue_id`` (never by re-submitting the paid queue).
        """
        if status not in _ALLOWED_STATUSES:
            raise OutputError(f"Job record status must be one of {sorted(_ALLOWED_STATUSES)}; got {status!r}")
        record = {
            "schema_version": SCHEMA_VERSION,
            "media_type": media_type,
            "model": model,
            "queue_id": queue_id,
            "status": status,
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
            "request_sha256": sha256_text(stable_json(request)),
            "input_summary": input_summary,
            "artifact": None,
            "last_response": None,
            "download_url_display": None,
            "download_url_secret_ref": None,  # nosec B105 - key name, not a credential
        }
        self._write(queue_id, record)
        return record

    def update(self, queue_id: str, **changes: Any) -> dict[str, Any]:
        path = self._path(queue_id)
        _acquire_lock(path)
        try:
            record = self.get(queue_id)
            sanitized: dict[str, Any] = {}
            download_url_secret: str | None = None
            for key, value in changes.items():
                if key == "download_url":
                    if isinstance(value, str) and value:
                        display = _sanitize_download_url_for_display(value)
                        sanitized["download_url_display"] = display
                        download_url_secret = value
                        # Drop any stale legacy field so an operator who
                        # inspects the record via ``jobs get`` cannot mistake
                        # the redacted v2-style field for a live URL.
                        sanitized.pop("download_url", None)
                    else:
                        # ``download_url=None`` clears the sidecar AND the
                        # display value, but only if the caller is
                        # explicitly clearing it.
                        sanitized["download_url_display"] = None
                        sanitized["download_url_secret_ref"] = None
                        sanitized.pop("download_url", None)
                elif key == "download_url_display":
                    sanitized[key] = value
                elif key == "download_url_secret_ref":
                    # Callers should not normally set this directly; it
                    # belongs to ``download_url`` triggers. Accept it but
                    # do not leak stored credentials through the record.
                    sanitized[key] = value
                else:
                    sanitized[key] = _sanitize_record_payload(value)
            record.update(sanitized)
            record["updated_at"] = utc_now_iso()
            self._write(queue_id, record)
            if download_url_secret is not None:
                secret_path = self._secret_path(queue_id)
                tmp_path = secret_path.with_suffix(".url.tmp")
                tmp_path.write_text(download_url_secret, encoding="utf-8")
                with suppress(OSError):
                    os.chmod(tmp_path, 0o600)
                os.replace(tmp_path, secret_path)
                with suppress(OSError):
                    os.chmod(secret_path, 0o600)
                record["download_url_secret_ref"] = str(secret_path)
                # Persist the absolute sidecar path back into the record so
                # ``download_url_for`` can locate the sidecar even if the
                # root path changes between sessions (rare, but possible
                # when the user moves their jobs dir).
                self._write(queue_id, record)
            return record
        finally:
            _release_lock(path)

    def get(self, queue_id: str) -> dict[str, Any]:
        record = self._load_raw(queue_id)
        if not isinstance(record, dict):
            raise OutputError(f"Job record for {queue_id} is not a JSON object.")
        # Migrate legacy v2 records so callers always see the v3 shape.
        if record.get("schema_version") != SCHEMA_VERSION:
            return self._migrate_legacy_record(record)
        return record

    def _migrate_legacy_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Upgrade a pre-v3 record to the v3 shape in-memory.

        Sets ``schema_version`` to :data:`SCHEMA_VERSION`, moves any
        existing redacted ``download_url`` into ``download_url_display``,
        leaves ``download_url_secret_ref`` as ``None`` (the underlying
        signed URL has already been discarded by the prior redaction
        path, so the operator cannot download from a v2 record — this
        is intentional, callers must re-fetch the URL from Venice).
        """
        upgraded = dict(record)
        if "download_url" in upgraded:
            upgraded["download_url_display"] = _sanitize_legacy_download_url(upgraded.pop("download_url"))
        else:
            upgraded.setdefault("download_url_display", None)
        upgraded.setdefault("download_url_secret_ref", None)
        upgraded["schema_version"] = SCHEMA_VERSION
        return upgraded

    def _load_raw(self, queue_id: str) -> dict[str, Any]:
        path = self._path(queue_id)
        if not path.is_file():
            raise OutputError(f"No local job record exists for queue_id={queue_id}")
        try:
            payload = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            raise OutputError(f"Unable to read job record {path}: {exc}") from exc
        return payload

    def list(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*.json"), reverse=True):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("schema_version") != SCHEMA_VERSION:
                payload = self._migrate_legacy_record(payload)
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
