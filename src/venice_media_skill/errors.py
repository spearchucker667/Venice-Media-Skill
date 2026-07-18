"""Typed failures exposed by the bridge."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class VeniceMediaError(RuntimeError):
    """Base class for expected bridge failures."""


class ConfigurationError(VeniceMediaError):
    """Invalid or missing local configuration."""


class RequestValidationError(VeniceMediaError):
    """A request manifest is malformed or unsupported."""


class OutputError(VeniceMediaError):
    """Generated media could not be decoded or persisted."""


class PayloadValidationError(RequestValidationError):
    """A caller-supplied field violates the operation's typed contract."""


class ReservedParameterError(PayloadValidationError):
    """A reserved or transport-control key appeared inside ``parameters``."""

    def __init__(self, key: str, *, context: str = "parameters") -> None:
        super().__init__(
            f"Reserved key {key!r} is not permitted inside {context}; provide the value at a top-level field."
        )
        self.key = key
        self.context = context


@dataclass(slots=True)
class ConsentChallenge:
    """A persisted 409 Seedance consent challenge bound to a specific request."""

    challenge_id: str
    operation: str
    model: str
    payload_hash: str
    input_hashes: tuple[str, ...]
    consent_version: str
    policy_text: str
    consent_flow: str
    face_media_roles: tuple[str, ...]
    docs_url: str
    created_at: str
    expires_at: str


class ConsentApprovalRequired(VeniceMediaError):
    """A Seedance consent challenge is awaiting explicit user approval."""

    def __init__(self, challenge: ConsentChallenge) -> None:
        super().__init__(f"Seedance consent challenge {challenge.challenge_id} awaits explicit approval.")
        self.challenge = challenge


class ConsentApprovalMissing(VeniceMediaError):
    """The user attempted to submit consents without a matching approval."""

    def __init__(self, payload_hash: str) -> None:
        super().__init__(
            f"No approved consent matches payload hash {payload_hash}; "
            f"approve the challenge via the CLI before resubmission."
        )
        self.payload_hash = payload_hash


@dataclass(slots=True)
class QuoteApproval:
    """A persisted, hash-bound quote approval.

    Holds exactly what the user reviewed so any divergence at queue time is
    rejected with a deterministic error. ``model`` is *not* stored because
    Venice quotes are operation-keyed, not model-keyed — the model flows in
    with the queue body and is verified independently.
    """

    approval_id: str
    operation: str
    payload_hash: str
    quote_response: dict[str, Any]
    max_cost: float
    expires_at: str
    created_at: str = ""


class QuoteApprovalRequired(VeniceMediaError):
    """A paid queued request requires an explicit, hash-bound quote approval."""

    def __init__(self, *, operation: str, payload_hash: str, quote: dict[str, Any]) -> None:
        super().__init__(
            f"Approved quote required for {operation} (payload_hash={payload_hash}); "
            f"run `venice-media approve-quote` after reviewing the quote."
        )
        self.operation = operation
        self.payload_hash = payload_hash
        self.quote = quote


class QuoteApprovalMismatch(VeniceMediaError):
    """Quote approval exists but the queued payload is no longer the approved one."""

    def __init__(self, *, approved_hash: str, current_hash: str) -> None:
        super().__init__(
            f"Approved payload hash {approved_hash} does not match current "
            f"payload hash {current_hash}; the request must be re-approved."
        )
        self.approved_hash = approved_hash
        self.current_hash = current_hash


class PayloadHashMismatch(VeniceMediaError):
    """Internal hashes diverged during normalization; the request is unsafe."""

    def __init__(self, *, expected: str, actual: str, context: str) -> None:
        super().__init__(f"Payload hash mismatch in {context}: expected {expected}, computed {actual}.")
        self.expected = expected
        self.actual = actual
        self.context = context


class ContentValidationError(VeniceMediaError):
    """Declared MIME/signature mismatch on downloaded or decoded media."""

    def __init__(
        self,
        *,
        declared: str,
        detected: str | None,
        reason: str,
        sha256: str | None = None,
    ) -> None:
        super().__init__(
            f"Content validation failed for declared {declared!r}: {reason} (detected={detected!r}, sha256={sha256})"
        )
        self.declared = declared
        self.detected = detected
        self.reason = reason
        self.sha256 = sha256


class NetworkSafetyError(VeniceMediaError):
    """Network target failed safety validation before fetch completed."""

    def __init__(
        self,
        *,
        url: str,
        reason: str,
        resolved_ip: str | None = None,
    ) -> None:
        super().__init__(f"Unsafe URL {url}: {reason}")
        self.url = url
        self.reason = reason
        self.resolved_ip = resolved_ip


class DownloadLimitExceeded(NetworkSafetyError):
    """Allowed byte budget for a streaming download was exceeded."""

    def __init__(self, *, url: str, limit: int, observed: int) -> None:
        super().__init__(
            url=url,
            reason=f"download exceeded {limit} bytes (observed {observed})",
        )
        self.limit = limit
        self.observed = observed


class PublicHttpError(NetworkSafetyError):
    """HTTP error status on a public (unauthenticated) media download.

    Distinct from :class:`ApiError` because the bridge never attaches an
    ``Authorization`` header to public downloads and the host is not
    necessarily a Venice API endpoint. The error carries the status code,
    request URL, declared content type, request id from the response
    headers (``x-request-id`` / ``request-id``), and a bounded,
    ``utf-8``-safe body preview so callers can produce actionable
    diagnostics without losing fail-closed behavior.
    """

    BODY_PREVIEW_LIMIT = 512

    def __init__(
        self,
        *,
        url: str,
        status_code: int,
        message: str,
        content_type: str = "",
        request_id: str | None = None,
        body_preview: str = "",
    ) -> None:
        super().__init__(
            url=url,
            reason=f"HTTP {status_code} from public media host: {message}",
        )
        self.status_code = status_code
        self.content_type = content_type
        self.request_id = request_id
        self.body_preview = body_preview


@dataclass(slots=True)
class ApiError(VeniceMediaError):
    """A non-success response from the Venice API."""

    status_code: int
    message: str
    payload: Any = None
    request_id: str | None = None
    cause: str | None = None

    def __str__(self) -> str:
        suffix_parts: list[str] = []
        if self.request_id:
            suffix_parts.append(f"request_id={self.request_id}")
        if self.cause:
            suffix_parts.append(f"cause={self.cause}")
        suffix = (" (" + ", ".join(suffix_parts) + ")") if suffix_parts else ""
        return f"Venice API returned HTTP {self.status_code}: {self.message}{suffix}"


@dataclass(slots=True)
class TransportError(VeniceMediaError):
    """DNS, connection, TLS, timeout, or other HTTP transport failure."""

    message: str
    cause: str

    def __str__(self) -> str:
        return f"Venice API transport failed: {self.message} (cause={self.cause})"


@dataclass(slots=True)
class ConsentRequired(VeniceMediaError):
    """Seedance detected face media and requires an explicit legal attestation.

    Always carries the raw 409 payload so callers can render the provider
    policy verbatim. Implementations should persist a ``ConsentChallenge`` and
    require a separate approve-consent command rather than auto-resubmit.
    """

    payload: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return (
            "Seedance face-media consent is required. The next step is to run "
            "`venice-media approve-consent <challenge_id>` after reviewing the "
            "challenge and policy text returned by the provider."
        )
