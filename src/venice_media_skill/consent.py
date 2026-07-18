"""Seedance consent challenge and quote approval state machines.

A challenge is born when the Venice API returns a ``409 needs_consent``
response. The bridge persists a :class:`ConsentChallenge` keyed off the
*exact bytes* the provider must receive on resubmission. The host agent
reviews the policy and invokes ``approve-consent`` with the exact
maximum cost they accept; only at that point does the runner attach
``consents.seedance`` to the queued payload.

For paid queued video/audio generation, an analogous
:class:`QuoteApproval` binds the quoted response, the maximum cost the
operator is willing to spend, and a payload hash. Any divergence at
queue time causes :class:`~venice_media_skill.errors.QuoteApprovalMismatch`.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final, cast

from .errors import (
    ConsentApprovalMissing,
    ConsentChallenge,
    QuoteApproval,
    QuoteApprovalMismatch,
)
from .util import utc_now_iso

_LOCK_DIR = os.environ.get("VENICE_MEDIA_LOCK_DIR") or (
    "/tmp/venice-media-locks" if Path("/tmp").exists() else os.environ.get("TEMP", str(Path.home()))
)


def _get_lock_path(path: Path) -> Path:
    """Get the lock file path for a given data file."""
    return Path(_LOCK_DIR) / (path.name + ".lock")


def _acquire_lock(path: Path, exclusive: bool = True, timeout: float = 10.0) -> None:
    """Acquire a file-based lock. Works on both Unix and Windows."""
    lock_path = _get_lock_path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    while True:
        try:
            # Use atomic file creation for exclusive lock
            if exclusive:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return
            # For shared lock, just check if lock file exists
            if not lock_path.exists():
                return
        except (FileExistsError, PermissionError, OSError):
            pass
        if time.monotonic() - start > timeout:
            raise TimeoutError(f"Could not acquire lock on {path} within {timeout}s")
        time.sleep(0.05)


def _release_lock(path: Path, exclusive: bool = True) -> None:
    """Release a file-based lock."""
    lock_path = _get_lock_path(path)
    if exclusive:
        with contextlib.suppress(OSError):
            lock_path.unlink(missing_ok=True)


CHALLENGE_TTL_SECONDS: Final[int] = 7 * 24 * 3600
QUOTE_APPROVAL_TTL_SECONDS: Final[int] = 24 * 3600
DEFAULT_MAX_COST_FLOOR: Final[float] = 0.0


@dataclass(slots=True, frozen=True)
class ConsentApproval:
    """Connection between a persisted challenge and the user's confirmation.

    Persisted alongside :class:`ConsentChallenge` so the runner can verify
    the same ``payload_hash`` is being submitted along with the user's
    explicit acknowledgment of the policy text.
    """

    challenge_id: str
    approved_at: str
    expires_at: str
    payload_hash: str
    max_cost: float | None


def new_challenge_id() -> str:
    return "cnc_" + secrets.token_urlsafe(16)


def new_approval_id() -> str:
    return "qap_" + secrets.token_urlsafe(16)


class ConsentStore:
    """File-backed store of consent challenges and approvals."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write_seed()

    def _write_seed(self) -> None:
        from .output import atomic_write_text

        atomic_write_text(self.path, json.dumps({"challenges": {}, "approvals": {}}, indent=2, sort_keys=True) + "\n")

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"challenges": {}, "approvals": {}}
        loaded = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            return {"challenges": {}, "approvals": {}}
        return cast(dict[str, Any], loaded)

    def _write(self, data: dict[str, Any]) -> None:
        from .output import atomic_write_text  # local import to break cycle

        atomic_write_text(self.path, json.dumps(data, indent=2, sort_keys=True) + "\n")

    # -- challenges ---------------------------------------------------------

    def record_challenge(
        self,
        *,
        operation: str,
        model: str,
        payload_hash: str,
        input_hashes: tuple[str, ...],
        provider_payload: Any,
    ) -> ConsentChallenge:
        challenge_id = new_challenge_id()
        provider_mapping: Mapping[str, Any] = provider_payload if isinstance(provider_payload, Mapping) else {}
        consent = provider_mapping.get("consent")
        if not isinstance(consent, Mapping):
            consent = {}
        policy = provider_mapping.get("policy")
        if not isinstance(policy, Mapping):
            policy = {}
        face_roles = provider_mapping.get("face_media_roles")
        if not isinstance(face_roles, list):
            face_roles = []

        challenge = ConsentChallenge(
            challenge_id=challenge_id,
            operation=operation,
            model=model,
            payload_hash=payload_hash,
            input_hashes=tuple(input_hashes),
            consent_version=str(consent.get("consent_version", "")) if consent else "",
            policy_text=str(consent.get("policy_text", "")) if consent else "",
            consent_flow=str(provider_mapping.get("consent_flow", "seedance")),
            face_media_roles=tuple(str(role) for role in face_roles),
            docs_url=str(provider_mapping.get("docs_url", "")),
            created_at=utc_now_iso(),
            expires_at=_iso_after_seconds(CHALLENGE_TTL_SECONDS),
        )
        _acquire_lock(self.path, exclusive=True)
        try:
            data = self._read()
            data["challenges"][challenge_id] = asdict(challenge)
            from .output import atomic_write_text

            atomic_write_text(self.path, json.dumps(data, indent=2, sort_keys=True) + "\n")
        finally:
            _release_lock(self.path, exclusive=True)
        return challenge

    def load_challenge(self, challenge_id: str) -> ConsentChallenge | None:
        _acquire_lock(self.path, exclusive=False)
        try:
            data = self._read()
        finally:
            _release_lock(self.path, exclusive=False)
        payload = data.get("challenges", {}).get(challenge_id)
        if payload is None:
            return None
        challenge = ConsentChallenge(**payload)
        if _is_expired(challenge.expires_at):
            return None
        return challenge

    # -- approvals ---------------------------------------------------------

    def approve(
        self,
        *,
        challenge_id: str,
        confirmed_max_cost: float | None,
        acknowledge_policy: bool,
    ) -> ConsentApproval:
        challenge = self.load_challenge(challenge_id)
        if challenge is None:
            raise ConsentApprovalMissing("unknown")
        if not acknowledge_policy:
            raise ConsentApprovalMissing("policy-unacknowledged")
        approval = ConsentApproval(
            challenge_id=challenge_id,
            approved_at=utc_now_iso(),
            expires_at=challenge.expires_at,
            payload_hash=challenge.payload_hash,
            max_cost=confirmed_max_cost,
        )
        _acquire_lock(self.path, exclusive=True)
        try:
            data = self._read()
            data.setdefault("approvals", {})[challenge.challenge_id] = asdict(approval)
            from .output import atomic_write_text

            atomic_write_text(self.path, json.dumps(data, indent=2, sort_keys=True) + "\n")
        finally:
            _release_lock(self.path, exclusive=True)
        return approval

    def approval_for(self, payload_hash: str) -> ConsentApproval | None:
        _acquire_lock(self.path, exclusive=False)
        try:
            data = self._read()
        finally:
            _release_lock(self.path, exclusive=False)
        approvals = data.get("approvals", {})
        for entry in approvals.values():
            if entry.get("payload_hash") != payload_hash:
                continue
            if _is_expired(entry.get("expires_at", "")):
                continue
            return ConsentApproval(**entry)
        return None


class QuoteApprovalStore:
    """Persists hash-bound quote approvals for paid queued operations."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({})

    def record(
        self,
        *,
        operation: str,
        model: str,
        payload_hash: str,
        quote_response: Mapping[str, Any],
        max_cost: float,
    ) -> QuoteApproval:
        approval = QuoteApproval(
            approval_id=new_approval_id(),
            operation=operation,
            model=model,
            payload_hash=payload_hash,
            quote_response=dict(quote_response),
            max_cost=float(max_cost),
            created_at=utc_now_iso(),
            expires_at=_iso_after_seconds(QUOTE_APPROVAL_TTL_SECONDS),
        )
        _acquire_lock(self.path)
        try:
            data = self._read()
            data[approval.approval_id] = _serialize_quote_approval(approval)
            self._write(data)
        finally:
            _release_lock(self.path)
        return approval

    def consume(self, *, approval_id: str, current_payload_hash: str, max_observed_cost: float | None) -> QuoteApproval:
        _acquire_lock(self.path)
        try:
            data = self._read()
            entry = data.get(approval_id)
            if entry is None:
                raise ConsentApprovalMissing(approval_id)
            if _is_expired(entry["expires_at"]):
                raise ConsentApprovalMissing("expired")
            approval = QuoteApproval(**entry)
            if approval.payload_hash != current_payload_hash:
                raise QuoteApprovalMismatch(approved_hash=approval.payload_hash, current_hash=current_payload_hash)
            _exceeds_max_cost = (
                max_observed_cost is not None
                and approval.max_cost is not None
                and max_observed_cost > approval.max_cost
            )
            if _exceeds_max_cost:
                raise ConsentApprovalMissing(
                    f"quote exceeded approved max_cost {approval.max_cost} (observed {max_observed_cost})"
                )
            # Single-use approvals: remove once consumed.
            del data[approval_id]
            self._write(data)
        finally:
            _release_lock(self.path)
        return approval

    def resolve(self, payload_hash: str) -> QuoteApproval | None:
        _acquire_lock(self.path, exclusive=False)
        try:
            data = self._read()
        finally:
            _release_lock(self.path, exclusive=False)
        for entry in data.values():
            if entry.get("payload_hash") != payload_hash:
                continue
            if _is_expired(entry.get("expires_at", "")):
                continue
            return QuoteApproval(**entry)
        return None

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        loaded = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            return {}
        return cast(dict[str, Any], loaded)

    def _write(self, data: dict[str, Any]) -> None:
        from .output import atomic_write_text  # local import to break cycle

        atomic_write_text(self.path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def _serialize_quote_approval(approval: QuoteApproval) -> dict[str, Any]:
    return {
        "approval_id": approval.approval_id,
        "operation": approval.operation,
        "model": approval.model,
        "payload_hash": approval.payload_hash,
        "quote_response": approval.quote_response,
        "max_cost": approval.max_cost,
        "created_at": approval.created_at,
        "expires_at": approval.expires_at,
    }


def _iso_after_seconds(seconds: int) -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


def _is_expired(iso: str) -> bool:
    if not iso:
        return True
    from datetime import UTC, datetime

    try:
        deadline = datetime.fromisoformat(iso)
    except ValueError:
        return True
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    return deadline <= datetime.now(UTC)


def ensure_seedance_fact(payload: Any) -> bool:
    """True when the Venice 409 payload actually carries ``needs_consent``."""
    if not isinstance(payload, Mapping):
        return False
    err = payload.get("error")
    if isinstance(err, Mapping) and err.get("code") == "needs_consent":
        return True
    return "needs_consent" in payload and "consent_flow" in payload


def build_consent_object(policy_version: str) -> dict[str, Any]:
    """Construct the on-wire Seedance consent confirmation block.

    The bridge never pre-grants this. The CLI persists a
    :class:`ConsentApproval` from an explicit user invocation; the runner
    only marshals this object onto a queue payload whose hash matches the
    approval.

    Provider expects exactly:
    {"consents": {"seedance": {"confirmed_terms_and_privacy": true,
    "confirmed_legal_right": true, "confirmed_screening_acknowledged": true}}}
    """
    return {
        "confirmed_terms_and_privacy": True,
        "confirmed_legal_right": True,
        "confirmed_screening_acknowledged": True,
    }


def quote_cost(quote_response: Mapping[str, Any]) -> float | None:
    raw = quote_response.get("quote")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    return None


__all__ = [
    "DEFAULT_MAX_COST_FLOOR",
    "ConsentApproval",
    "ConsentStore",
    "QuoteApprovalStore",
    "build_consent_object",
    "ensure_seedance_fact",
    "quote_cost",
]
