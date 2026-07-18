"""Typed failures exposed by the bridge."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

_QUERY_REDACT_INNER: re.Pattern[str] = re.compile(
    r"((?:token|key|secret|signature|sig|api_key|access_token|keyid|expires)=)[^&]+",
    re.IGNORECASE,
)


def _redact_url_for_display(url: str) -> str:
    """Return a host/path with the query blanked for safe logging.

    Preserves scheme/netloc/path so the operator can recognise the
    target while ensuring signature-bearing query tokens cannot be
    scraped even if the message is forwarded. Use
    :func:`_url_query_redacted` to confirm whether the original URL had
    a query or fragment in the first place.
    """
    if not url:
        return url
    parts = urlsplit(url)
    if not parts.scheme:
        return url
    has_query = bool(parts.query) or bool(parts.fragment)
    rebuilt = parts._replace(query="", fragment="")
    base = rebuilt.geturl()
    return f"{base}?[…redacted]" if has_query else base


def _url_query_redacted(url: str) -> bool:
    if not url:
        return False
    parts = urlsplit(url)
    return bool(parts.query) or bool(parts.fragment)


def _url_sha256(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _scrub_body_preview(body: str) -> str:
    """Sanitize an error body so any embedded signed URLs are redacted.

    Walks the substring looking for absolute ``http://`` / ``https://``
    URLs and replaces the query/fragment portion so a leaked
    ?signature=… or ?token=… token never reaches logs the operator can
    see. Non-URL substrings pass through unchanged.
    """
    if not body:
        return body
    pattern = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)

    def repl(match: re.Match[str]) -> str:
        url = match.group(0)
        # Trim common trailing punctuation that is unlikely to be part
        # of an actual URL.
        trailing = ""
        while url and url[-1] in ".,);]":
            trailing = url[-1] + trailing
            url = url[:-1]
        return f"{url.rstrip()}{trailing}" if not _url_query_redacted(url) else _redact_url_for_display(url) + trailing

    return pattern.sub(repl, body)


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
        # The ``url`` exposed on the exception (and via ``str(exc)`` /
        # ``repr(exc)``) is redacted: scheme/netloc/path only, with the
        # query and fragment collapsed into ``?[…redacted]`` so an
        # attacker scraping logs cannot recover a signed payload even
        # if the message reaches external surfaces. ``url_sha256`` is a
        # stable fingerprint for correlation, and ``query_redacted``
        # is a boolean telling the operator whether the original URL
        # ever had a query or fragment. Internal callers that need the
        # original signed URL must read it from the JobStore sidecar,
        # never from this exception.
        self.url = _redact_url_for_display(url)
        self.url_sha256 = _url_sha256(url)
        self.query_redacted = _url_query_redacted(url)
        super().__init__(
            f"Unsafe URL {self.url}{' [query/fragment redacted]' if self.query_redacted else ''}: {reason}"
        )
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
    diagnostics without losing fail-closed behavior. Both the URL and
    any URLs embedded in ``body_preview`` are redacted so signature
    tokens cannot leak through diagnostic surfaces.
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
        # Sanitize the body preview so any embedded URLs have their
        # query/fragment redacted; this is the surface that ends up in
        # operator-visible error messages.
        self.body_preview = _scrub_body_preview(body_preview)


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
class DurableQueueWriteFailed(VeniceMediaError):
    """Venice accepted a paid queued submission but the local record write failed.

    Carries the ``queue_id`` so the operator can recover the paid job
    via ``video.retrieve`` / ``audio.retrieve`` (never by re-approving
    and re-submitting). Quote and consent approvals are released rather
    than finalised when this fires, so an operator who attempts to
    re-approve for the same payload will surface a hash mismatch —
    preventing a second paid submission by accident. The runner never
    auto-resubmits in this state.
    """

    queue_id: str
    operation: str
    model: str
    media_type: str
    cause: str

    def __str__(self) -> str:
        return (
            f"Venice accepted paid {self.operation} (queue_id={self.queue_id}) "
            f"but the local durable record could not be written: {self.cause}. "
            "The runner will NOT auto-resubmit. Recover with "
            f"{self.media_type}.retrieve and parameters.queue_id={self.queue_id!r}."
        )


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
