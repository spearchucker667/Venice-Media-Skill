"""Minimal Venice HTTP client with explicit binary, consent, and redirect safety."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.metadata
import ipaddress
import os
import posixpath
import socket
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from .errors import (
    ApiError,
    ConfigurationError,
    ConsentRequired,
    DownloadLimitExceeded,
    NetworkSafetyError,
    PublicHttpError,
)

# Resolver injection: tests can substitute DNS resolution so security
# tests run deterministically without external DNS. Production callers
# pass ``None`` so ``socket.getaddrinfo`` (via ``_resolve_safely``) is
# consulted. Any caller-supplied resolver must still return globally
# routable IPs; ``_enforce_safe_target`` runs the ``is_global`` check on
# every entry.
Resolver = Callable[[str], Sequence[str]]


def _get_package_version() -> str:
    try:
        return importlib.metadata.version("venice-media-skill")
    except importlib.metadata.PackageNotFoundError:
        return "0.1.0"


# Authenticated API endpoints MUST be limited to the canonical Venice API
# host so Bearer credentials never travel to a CDN or third-party cloud
# storage. ``allow_noncanonical_endpoint=True`` is the only escape hatch
# and is reserved for development and tests.
ALLOWED_API_HOSTS: frozenset[str] = frozenset({"api.venice.ai"})

# Pre-signed media URLs are reachable on these Venice-operated + cloud
# CDN hosts. Only consumed by the unauthenticated download path; this
# set MUST stay disjoint from the intent of ``ALLOWED_API_HOSTS`` so a
# poisoned ``base_url`` cannot coerce credential forwarding.
#
# Each entry must be backed by a documented Venice response contract
# before being added. The comments cite the OpenAPI/llms.txt field that
# references the host; review ``references/venice-openapi.yaml`` and
# ``references/venice-api-llms.md`` when extending the set.
ALLOWED_DOWNLOAD_HOSTS: frozenset[str] = frozenset(
    {
        # Streaming audio/video endpoints occasionally return media URLs
        # on the API host itself.
        "api.venice.ai",
        # Primary Venice media CDN. Used in OpenAPI example payloads,
        # e.g. ``https://cdn.venice.ai/avatar.png``.
        "cdn.venice.ai",
        # Secondary Venice media host for some model outputs.
        "media.venice.ai",
        # Venice operator root domain — legacy / canonical media URLs.
        "venice.ai",
        # Negotiation responses occasionally hand back a GCS signed URL.
        # The exact hostname lets the bridge reach the bucket but blocks
        # arbitrary caller-hosted subdomains (no ``.googleapis.com``
        # suffix acceptance).
        "storage.googleapis.com",
        # Cloudflare R2 bucket for some media negotiation responses.
        "r2.cloudflarestorage.com",
    }
)

# Suffix list covers Venice-operator-controlled subdomains only. Cloud-
# provider broad suffixes (``.amazonaws.com``, ``.googleapis.com``,
# ``.cloudflarestorage.com``) MUST NOT be added — that namespace admits
# unrelated tenants which defeats this SSRF allow-list. If a future
# Venice response contract requires a cloud-bucket subdomain, add its
# *exact* hostname to ``ALLOWED_DOWNLOAD_HOSTS`` instead.
ALLOWED_HOST_SUFFIXES: tuple[str, ...] = (".venice.ai",)

DEFAULT_PUBLIC_MAX_BYTES = 500 * 1024 * 1024

# In-memory default for :meth:`VeniceClient.download_public_bytes`. Bound
# tightly enough that a single oversized response cannot exhaust the host
# process — a 64 MiB body buffered through two intermediate copies still
# fits comfortably in any modern CLI subprocess. Callers who need the
# legacy 500 MiB ceiling must pass ``max_bytes=`` explicitly; callers
# expecting to download larger blobs should use
# :meth:`VeniceClient.download_public_file` so the response streams to
# disk instead of buffering in RAM.
IN_MEMORY_MAX_BYTES: int = 64 * 1024 * 1024

# File-sink default for :meth:`VeniceClient.download_public_file`.
# Accommodates the largest current Venice model outputs (multi-minute
# 1080p video). Callers may raise it further for unusually large media;
# the byte cap is enforced against the streaming counter so the bridge
# still aborts before exceeding the budget.
FILE_MAX_BYTES: int = 2 * 1024 * 1024 * 1024

# Minimum prefix needed for magic-byte validation against every media type
# we accept (mp4, webp/jpeg/png/wav/aac ID3, etc.). 4 KiB comfortably covers
# the largest signature window in :func:`fast_validate_content_type`.
_MAGIC_HEAD_BYTES: int = 4096

ALLOWED_DOWNLOAD_PORT = 443


@dataclass(frozen=True)
class DownloadHostPolicy:
    """Operator-curated allow-list for *unauthenticated* media downloads.

    Exact hosts are strongly preferred: each is enumerated above with
    the Venice response contract that documents it. ``suffixes`` must
    remain a Venice-operator namespace only; cloud-provider broad
    suffixes MUST NOT be added because they admit unrelated tenants
    and effectively whitelist the whole cloud root.

    The download path constructs a policy from any caller-supplied
    ``allowed_hosts`` plus the operator suffix list, but never from a
    caller-supplied suffix tuple — that authority belongs to this
    module. ``accepts`` is the single switch the safety check consults.
    """

    exact_hosts: frozenset[str]
    suffixes: tuple[str, ...] = ()

    def accepts(self, host: str) -> bool:
        lowered = host.lower()
        if lowered in self.exact_hosts:
            return True
        return any(lowered.endswith(suffix) for suffix in self.suffixes)


DEFAULT_DOWNLOAD_POLICY: DownloadHostPolicy = DownloadHostPolicy(
    exact_hosts=ALLOWED_DOWNLOAD_HOSTS,
    suffixes=ALLOWED_HOST_SUFFIXES,
)


@dataclass(slots=True)
class ApiResponse:
    status_code: int
    content_type: str
    headers: Mapping[str, str]
    json_data: Any | None = None
    content: bytes | None = None
    sha256: str | None = None
    path: str | None = None
    file_path: Path | None = None
    observed: int = 0

    @property
    def is_binary(self) -> bool:
        return self.content is not None or self.file_path is not None


class VeniceClient:
    """Thin synchronous client designed for deterministic CLI subprocess use.

    Security contract:
    - ``base_url`` must be HTTPS over the configured port (443). Plain HTTP
      base URLs are rejected to prevent exfiltrating the Authorization
      header.
    - ``follow_redirects`` is disabled. We validate every hop ourselves
      before issuing the next request. The implementation never follows
      redirects on authenticated API calls; the caller may opt-in explicitly
      for public download URLs only.
    - DNS errors fail closed.
    - ``VeniceClient`` carries the ``Authorization`` header only. It never
      forwards that header to a download URL, even when one is reachable.

    Known limitations (see ``docs/threat-model.md`` for the broader
    context):
    - DNS rebinding window: :meth:`download_public_url` validates a
      host's resolved address once, but ``httpx`` re-resolves the same
      hostname on socket connect. Mitigated by the strict
      ``DEFAULT_DOWNLOAD_POLICY`` allow-list; full IP pinning via a
      custom transport is future hardening (P1-1).
    - The public-download ``httpx.Client`` is created with
      ``trust_env=False`` so ``HTTP_PROXY`` / ``HTTPS_PROXY`` /
      ``NO_PROXY`` are ignored. Operators that need proxy support for
      media downloads must supply an explicit ``transport``.
    - The authenticated ``httpx.Client`` keeps ``trust_env`` at its
      httpx default to honor user-configured proxies for managed
      deployments; see ``docs/security-and-privacy.md``.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        timeout_seconds: float = 120.0,
        transport: httpx.BaseTransport | None = None,
        allow_noncanonical_endpoint: bool = False,
        resolver: Resolver | None = None,
    ) -> None:
        if not api_key:
            raise ConfigurationError("VENICE_API_KEY is required for API operations.")
        if not allow_noncanonical_endpoint and not _is_safe_base_url(base_url):
            raise ConfigurationError(
                f"base_url must be HTTPS and use the canonical Venice host; got: {base_url!r}. "
                f"To use an alternate endpoint, explicitly opt in with "
                f"allow_noncanonical_endpoint=True (development / test only)."
            )
        self._timeout_seconds = timeout_seconds
        self._resolver = resolver
        # ``follow_redirects=False`` — we never trust the server to redirect
        # us through Venice CDN hosts without prior validation.
        self._client = httpx.Client(
            base_url=base_url.rstrip("/") + "/",
            timeout=timeout_seconds,
            headers={
                "Authorization": f"Bearer {api_key}",
                "User-Agent": f"venice-media-skill/{_get_package_version()}",
                "Accept": "application/json, image/*, audio/*, video/*, text/plain",
            },
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> VeniceClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    # ---------------------------------------------------------------
    # public authenticated entry
    # ---------------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        files: Any = None,
    ) -> ApiResponse:
        path = _validate_api_path(path)
        try:
            response = self._client.request(
                method,
                path,
                json=json_body if json_body is not None else None,
                params=params,
                data=data,
                files=files,
            )
        except httpx.HTTPError as exc:
            from .errors import TransportError

            raise TransportError(message=str(exc), cause=type(exc).__name__) from exc

        # ``follow_redirects=False`` means we may have a 3xx status that
        # the user explicitly did not intend. Surface it as an error.
        if 300 <= response.status_code < 400:
            location = response.headers.get("location", "")
            raise ApiError(
                response.status_code,
                (
                    "Venice API returned a redirect; the bridge refuses to follow it on "
                    f"authenticated requests to prevent credential forwarding. Location: {location!r}"
                ),
                payload={"location": location},
            )

        # Special-case 409 consent challenges BEFORE the generic >=400
        # coercion so callers can branch on the consent_required surface.
        if response.status_code == 409:
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if isinstance(payload, dict) and (
                (isinstance(payload.get("error"), dict) and payload["error"].get("code") == "needs_consent")
                or "needs_consent" in payload
            ):
                raise ConsentRequired(payload=payload)

        return _coerce_response(response, path=path)

    def get_json(self, path: str, *, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        response = self.request("GET", path, params=params)
        if not isinstance(response.json_data, dict):
            raise ApiError(
                response.status_code,
                f"Venice endpoint {path} did not return an object.",
                payload=response.json_data,
            )
        return response.json_data

    # ---------------------------------------------------------------
    # public download (no Authorization header)
    # ---------------------------------------------------------------

    def download_public_url(
        self,
        url: str,
        *,
        max_bytes: int = DEFAULT_PUBLIC_MAX_BYTES,
        allowed_hosts: Sequence[str] | None = None,
        accepted_mime_prefixes: Sequence[str] = ("image/", "audio/", "video/", "application/", "text/"),
        transport: httpx.BaseTransport | None = None,
        resolver: Resolver | None = None,
    ) -> ApiResponse:
        """Back-compat shim — delegates to :meth:`download_public_bytes`.

        Preserves the historical 500 MiB ceiling so callers such as
        ``runner.MediaRunner`` (which downloads signed media URLs from
        Venice responses) and existing tests do not need to migrate.
        New callers should prefer :meth:`download_public_bytes` (explicit
        in-memory with the safer 64 MiB default) or :meth:`download_public_file`
        (atomic on-disk sink for large media).
        """
        return self.download_public_bytes(
            url,
            max_bytes=max_bytes,
            allowed_hosts=allowed_hosts,
            accepted_mime_prefixes=accepted_mime_prefixes,
            transport=transport,
            resolver=resolver,
        )

    def download_public_bytes(
        self,
        url: str,
        *,
        max_bytes: int = IN_MEMORY_MAX_BYTES,
        allowed_hosts: Sequence[str] | None = None,
        accepted_mime_prefixes: Sequence[str] = ("image/", "audio/", "video/", "application/", "text/"),
        transport: httpx.BaseTransport | None = None,
        resolver: Resolver | None = None,
    ) -> ApiResponse:
        """Stream ``url`` (HTTPS only) into memory with a bounded byte budget.

        Default :data:`IN_MEMORY_MAX_BYTES` (64 MiB) is intentionally
        conservative; callers needing larger media should use
        :meth:`download_public_file` so the on-disk sink avoids buffering
        the entire body. The helper enforces ``max_bytes`` *during* the
        in-flight chunk stream so an over-cap reply can never balloon
        the process.

        ``resolver`` overrides DNS resolution so tests can run without
        external DNS; production callers pass ``None`` (or accept the
        resolver passed to :class:`VeniceClient.__init__`).
        """
        return self._download_with_sink(
            url,
            sink=_MemorySink(),
            max_bytes=max_bytes,
            allowed_hosts=allowed_hosts,
            accepted_mime_prefixes=accepted_mime_prefixes,
            transport=transport,
            resolver=resolver,
        )

    def download_public_file(
        self,
        url: str,
        *,
        destination: Path,
        max_bytes: int = FILE_MAX_BYTES,
        allowed_hosts: Sequence[str] | None = None,
        accepted_mime_prefixes: Sequence[str] = ("image/", "audio/", "video/", "application/", "text/"),
        transport: httpx.BaseTransport | None = None,
        resolver: Resolver | None = None,
    ) -> ApiResponse:
        """Stream ``url`` to ``destination`` atomically with a bounded byte budget.

        Bytes are streamed into a sibling temp file (``.venice-media-*``)
        inside ``destination.parent`` so the rename onto ``destination``
        stays atomic on the same filesystem. The result is hashed in
        flight; on success :attr:`ApiResponse.file_path` points at
        ``destination`` and :attr:`ApiResponse.content` is ``None``.

        On any error path the temp file is removed and ``destination``
        is left untouched — the runner can re-attempt without losing
        the user-visible artifact path.
        """
        return self._download_with_sink(
            url,
            sink=_FileSink(destination),
            max_bytes=max_bytes,
            allowed_hosts=allowed_hosts,
            accepted_mime_prefixes=accepted_mime_prefixes,
            transport=transport,
            resolver=resolver,
        )

    def _download_with_sink(
        self,
        url: str,
        *,
        sink: _MemorySink | _FileSink,
        max_bytes: int,
        allowed_hosts: Sequence[str] | None,
        accepted_mime_prefixes: Sequence[str],
        transport: httpx.BaseTransport | None,
        resolver: Resolver | None,
    ) -> ApiResponse:
        """Common download pipeline used by both the bytes and file sinks.

        Owns the hop loop, redirect safety, 4xx/5xx preview capture,
        Content-Length pre-flight, magic-byte validation through a
        bounded head buffer, and the sink-finalization ceremony.

        Wraps the entire streaming block in ``try / except BaseException:
        sink.discard(); raise`` so abandoned downloads never leave a
        partial artifact at the caller's destination.
        """
        if not isinstance(url, str) or not url:
            raise NetworkSafetyError(url="", reason="empty URL")
        if not url.startswith("https://"):
            raise NetworkSafetyError(
                url=url,
                reason="only HTTPS is accepted for Venice-supplied media URLs",
            )

        allowed_hosts_set = frozenset(allowed_hosts) if allowed_hosts else ALLOWED_DOWNLOAD_HOSTS
        effective_resolver = resolver if resolver is not None else self._resolver
        visited: set[str] = set()
        current = url
        # ``trust_env=False`` blocks ``httpx`` from honoring
        # ``HTTP_PROXY`` / ``HTTPS_PROXY`` / ``NO_PROXY`` env vars on
        # the public-download client: a misconfigured user-level proxy
        # can be coerced into forwarding signed URLs to a third party
        # without ever passing our explicit allow-list.
        try:
            with httpx.Client(
                timeout=self._timeout_seconds,
                follow_redirects=False,
                trust_env=False,
                headers={"User-Agent": f"venice-media-skill/{_get_package_version()}"},
                transport=transport,
            ) as http_client:
                for _ in range(MAX_REDIRECT_HOPS):
                    _enforce_safe_target(current, allowed_hosts_set, resolver=effective_resolver)
                    try:
                        response_ctx = http_client.stream("GET", current)
                    except httpx.HTTPError as exc:
                        raise NetworkSafetyError(
                            url=current,
                            reason=f"transport error: {exc}",
                        ) from exc
                    with response_ctx as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            location = response.headers.get("location", "")
                            if not location:
                                raise NetworkSafetyError(
                                    url=current,
                                    reason="redirect without Location header",
                                )
                            # Accept relative redirects via ``urljoin``; an
                            # absolute ``location`` passes through unchanged.
                            next_url = urljoin(current, location)
                            try:
                                next_parsed = urlparse(next_url)
                            except ValueError as exc:
                                raise NetworkSafetyError(
                                    url=next_url,
                                    reason=f"unparseable redirect target: {exc}",
                                ) from exc
                            if (next_parsed.scheme or "").lower() != "https":
                                raise NetworkSafetyError(
                                    url=next_url,
                                    reason="redirect target is not HTTPS",
                                )
                            if not next_parsed.hostname:
                                raise NetworkSafetyError(
                                    url=next_url,
                                    reason="redirect target has no hostname",
                                )
                            # Cycle detection operates on the canonicalized
                            # URL — otherwise ``https://cdn.venice.ai/a``,
                            # ``https://cdn.venice.ai:443/a``, and
                            # ``https://CDN.VENICE.AI/a`` could be smuggled
                            # past the cycle check.
                            normalized_next = _normalize_url(next_url)
                            if normalized_next in visited:
                                raise NetworkSafetyError(
                                    url=next_url,
                                    reason="redirect cycle detected",
                                )
                            visited.add(normalized_next)
                            current = next_url
                            continue

                        if response.status_code >= 400:
                            declared = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                            request_id = (
                                response.headers.get("x-request-id")
                                or response.headers.get("request-id")
                                or response.headers.get("X-Request-Id")
                            )
                            # Capture a bounded body preview for diagnostics.
                            # The Response is mid-stream; reading up to the
                            # preview limit and stopping keeps the bridge
                            # fail-closed against oversized error bodies.
                            preview_buf = bytearray()
                            preview_limit = PublicHttpError.BODY_PREVIEW_LIMIT
                            for chunk in response.iter_bytes(chunk_size=preview_limit):
                                preview_buf.extend(chunk)
                                if len(preview_buf) >= preview_limit:
                                    preview_buf = preview_buf[:preview_limit]
                                    break
                            preview = preview_buf.decode("utf-8", errors="replace")
                            raise PublicHttpError(
                                url=current,
                                status_code=response.status_code,
                                message=(f"public media host returned HTTP {response.status_code}"),
                                content_type=declared,
                                request_id=request_id,
                                body_preview=preview,
                            )

                        declared = (
                            response.headers.get("content-type", "application/octet-stream")
                            .split(";", 1)[0]
                            .strip()
                            .lower()
                        )
                        if declared and not any(declared.startswith(prefix) for prefix in accepted_mime_prefixes):
                            raise NetworkSafetyError(
                                url=current,
                                reason=(f"declared content-type {declared!r} is not in the accepted prefix list"),
                            )

                        length_header = response.headers.get("content-length")
                        if length_header is not None:
                            try:
                                announced = int(length_header)
                            except ValueError as exc:
                                raise NetworkSafetyError(
                                    url=current,
                                    reason=f"non-integer Content-Length: {length_header!r}",
                                ) from exc
                            if announced > max_bytes:
                                raise DownloadLimitExceeded(url=current, limit=max_bytes, observed=announced)

                        # Magic-byte validation runs over the first
                        # ``_MAGIC_HEAD_BYTES`` of the body, then we
                        # forward the buffered prefix + remaining chunks
                        # through the sink. A small body smaller than
                        # the head threshold reaches the validator on
                        # the post-loop pass so a tiny MIME-claimed
                        # image cannot bypass the magic-byte gate.
                        from .util import fast_validate_content_type  # local import to avoid cycle

                        head = bytearray()
                        head_validated = False
                        for chunk in response.iter_bytes(chunk_size=64 * 1024):
                            if not head_validated:
                                head.extend(chunk)
                                if len(head) >= _MAGIC_HEAD_BYTES:
                                    try:
                                        fast_validate_content_type(
                                            bytes(head),
                                            declared or "application/octet-stream",
                                        )
                                    except Exception as exc:
                                        raise NetworkSafetyError(
                                            url=current,
                                            reason=(f"downloaded bytes failed fail-closed content validation: {exc}"),
                                        ) from exc
                                    head_validated = True
                                    sink.write(
                                        bytes(head),
                                        url=current,
                                        max_bytes=max_bytes,
                                    )
                                    head.clear()
                            else:
                                # On overflow inside ``sink.write``, the
                                # sink raises ``DownloadLimitExceeded``
                                # after self-discarding (file mode) or
                                # self-marking (memory mode); the outer
                                # ``try`` propagates the failure and
                                # ``sink.discard()`` runs once more.
                                sink.write(chunk, url=current, max_bytes=max_bytes)
                        if not head_validated and head:
                            try:
                                fast_validate_content_type(
                                    bytes(head),
                                    declared or "application/octet-stream",
                                )
                            except Exception as exc:
                                raise NetworkSafetyError(
                                    url=current,
                                    reason=(f"downloaded bytes failed fail-closed content validation: {exc}"),
                                ) from exc
                            sink.write(
                                bytes(head),
                                url=current,
                                max_bytes=max_bytes,
                            )

                        finalized = sink.finalize()
                        return ApiResponse(
                            status_code=response.status_code,
                            content_type=declared or "application/octet-stream",
                            headers=dict(response.headers),
                            content=finalized.body or None,
                            json_data=None,
                            sha256=finalized.sha256,
                            path=current,
                            file_path=finalized.file_path,
                        )
        except BaseException:
            # Atomicity: any failed download must not leave a partial
            # artifact at the caller's destination. Memory-mode sinks
            # are no-ops here; file-mode sinks unlink the temp file.
            sink.discard()
            raise
        raise NetworkSafetyError(url=current, reason="too many redirects")


MAX_REDIRECT_HOPS = 5


# ---------------------------------------------------------------
# Download sinks
# ---------------------------------------------------------------


@dataclass(slots=True)
class _Finalized:
    body: bytes
    file_path: Path | None
    sha256: str
    observed: int


class _MemorySink:
    """Sink that accumulates bytes in memory.

    Used by ``download_public_bytes`` and the legacy ``download_public_url``
    wrapper. ``discard()`` is a no-op other than flipping the discard flag
    to drop any further ``write`` calls.
    """

    def __init__(self) -> None:
        self._chunks: list[bytes] = []
        self._hasher = hashlib.sha256()
        self.observed: int = 0
        self._discarded: bool = False

    def write(self, chunk: bytes, *, url: str, max_bytes: int) -> None:
        if self._discarded:
            return
        self.observed += len(chunk)
        if self.observed > max_bytes:
            raise DownloadLimitExceeded(url=url, limit=max_bytes, observed=self.observed)
        self._chunks.append(chunk)
        self._hasher.update(chunk)

    def finalize(self) -> _Finalized:
        if self._discarded:
            raise RuntimeError("MemorySink was discarded and cannot be finalized")
        body = b"".join(self._chunks)
        if len(body) == 0:
            raise NetworkSafetyError(url="", reason="empty download not allowed")
        return _Finalized(
            body=body,
            file_path=None,
            sha256=self._hasher.hexdigest(),
            observed=self.observed,
        )

    def discard(self) -> None:
        self._discarded = True


class _FileSink:
    """Sink that streams bytes into a tmpfile next to ``destination``.

    Hashes while writing. On ``finalize()`` we ``flush`` + ``fsync`` the
    open file handle and ``os.replace`` onto the destination path so the
    output either contains the complete file or is absent. On any error
    path (``discard`` or size overflow) we unlink the tmpfile and leave
    ``destination`` untouched.
    """

    def __init__(self, destination: Path) -> None:
        self.destination = destination
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path_str = tempfile.mkstemp(prefix=".venice-media-", dir=str(self.destination.parent))
        os.close(fd)
        self._tmp_path = Path(tmp_path_str)
        self._fp = self._tmp_path.open("wb")
        self._hasher = hashlib.sha256()
        self.observed: int = 0
        self._discarded: bool = False

    def write(self, chunk: bytes, *, url: str, max_bytes: int) -> None:
        if self._discarded:
            return
        self.observed += len(chunk)
        if self.observed > max_bytes:
            self.discard()
            raise DownloadLimitExceeded(url=url, limit=max_bytes, observed=self.observed)
        self._fp.write(chunk)
        self._hasher.update(chunk)

    def finalize(self) -> _Finalized:
        if self._discarded:
            raise RuntimeError("FileSink was discarded and cannot be finalized")
        # Reject empty downloads BEFORE we touch ``self.destination`` so
        # an existing artifact is preserved — no ``os.replace`` followed
        # by ``unlink`` race window here.
        if self.observed == 0:
            self.discard()
            raise NetworkSafetyError(url="", reason="empty download not allowed")
        self._fp.flush()
        os.fsync(self._fp.fileno())
        self._fp.close()
        os.replace(self._tmp_path, self.destination)
        return _Finalized(
            body=b"",
            file_path=self.destination,
            sha256=self._hasher.hexdigest(),
            observed=self.observed,
        )

    def discard(self) -> None:
        if self._discarded:
            return
        self._discarded = True
        with contextlib.suppress(Exception):
            self._fp.close()
        with contextlib.suppress(Exception):
            self._tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------
# validation helpers
# ---------------------------------------------------------------


def _validate_api_path(path: str) -> str:
    """Reject any non-path ``path`` argument before handing it to ``httpx``.

    The authenticated :py:meth:`VeniceClient.request` only accepts absolute
    paths under the configured ``base_url``.  HTTPX will accept an absolute
    URL, a scheme-relative URL (``//evil.example/foo``), or a path-only
    string — and would forward the ``Authorization`` Bearer header to the
    resulting host.  We refuse anything except an absolute path that begins
    with a single ``/`` and contains no scheme, netloc, fragment, or query.
    """
    if not isinstance(path, str) or not path:
        raise NetworkSafetyError(
            url="",
            reason="authenticated request path must be a non-empty string",
        )
    if path.startswith("//"):
        raise NetworkSafetyError(
            url=path,
            reason="scheme-relative URLs are not permitted on authenticated requests",
        )
    if not path.startswith("/"):
        # An absolute or relative-without-leading-slash URL means the caller
        # is trying to redirect the request away from ``base_url``.
        raise NetworkSafetyError(
            url=path,
            reason="authenticated request path must begin with '/'",
        )
    parsed = urlparse(path)
    if parsed.scheme or parsed.netloc or parsed.params.startswith("//"):
        raise NetworkSafetyError(
            url=path,
            reason="authenticated request path must not contain a scheme, host, or netloc",
        )
    return path


def _is_safe_base_url(base_url: str) -> bool:
    """Authenticated API base URLs must match the strict ``ALLOWED_API_HOSTS`` set.

    Only ``api.venice.ai`` is acceptable by default; CDN hosts (cdn.venice.ai,
    storage.googleapis.com, r2.cloudflarestorage.com, etc.) appear here as
    regression targets — accepting them would let a poisoned ``base_url``
    redirect Bearer credentials into third-party hosting.
    """
    if not isinstance(base_url, str) or not base_url:
        return False
    parsed = urlparse(base_url)
    if parsed.scheme.lower() != "https":
        return False
    host = parsed.hostname
    if not host:
        return False
    if (parsed.port or 443) != ALLOWED_DOWNLOAD_PORT:
        return False
    return host.lower() in ALLOWED_API_HOSTS


def _enforce_safe_target(
    url: str,
    allowed_hosts: frozenset[str],
    *,
    resolver: Resolver | None = None,
) -> None:
    """Validate ``url`` against the host allow-list and global-IP requirement.

    Consults :data:`DEFAULT_DOWNLOAD_POLICY` to accept the host and the
    caller-supplied ``allowed_hosts`` for extras. Resolves ``host``
    and rejects loopback / private / link-local / reserved / metadata
    addresses.

    Limitation (P1-1 / threat model): this resolves the host once, but
    ``httpx`` re-resolves the same hostname when it opens the TCP
    connection. The DNS-rebinding window is mitigated solely by the
    strict policy allow-list above; the bridge does not pin the
    validated IP today. Future hardening will use a custom
    ``httpx.Transport`` that connects to the validated IP and preserves
    SNI/Host for TLS verification.
    """
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise NetworkSafetyError(url=url, reason=f"unparseable URL: {exc}") from exc
    if parsed.scheme.lower() != "https":
        raise NetworkSafetyError(url=url, reason="scheme must be https")
    host = (parsed.hostname or "").lower()
    if not host:
        raise NetworkSafetyError(url=url, reason="missing hostname")
    if (parsed.port or 443) != ALLOWED_DOWNLOAD_PORT:
        raise NetworkSafetyError(url=url, reason="non-standard port")
    # ``allowed_hosts`` is the *exact* extra-host list the caller asked
    # us to accept on top of the operator-curated policy. Anything not
    # in the policy (and not in the caller's exact list) is rejected —
    # including ARNs, glob patterns, and broad cloud suffixes that
    # could otherwise impersonate the Venice media CDN.
    if host not in allowed_hosts and not DEFAULT_DOWNLOAD_POLICY.accepts(host):
        raise NetworkSafetyError(
            url=url,
            reason=f"host {host!r} is not in the download allow-list",
        )
    resolver_ips = _resolve_safely(host) if resolver is None else _run_resolver(resolver, host)
    if not resolver_ips:
        raise NetworkSafetyError(
            url=url,
            reason=f"DNS resolution failed or returned no addresses for {host!r}",
            resolved_ip=None,
        )
    for ip in resolver_ips:
        ip_obj = ipaddress.ip_address(ip)
        if not ip_obj.is_global:
            raise NetworkSafetyError(
                url=url,
                reason=(
                    f"resolved address {ip_obj} is non-global (loopback, private, link-local, reserved, or metadata)"
                ),
                resolved_ip=str(ip_obj),
            )


def _resolve_safely(host: str) -> list[str]:
    """Resolve ``host`` while explicitly failing closed on DNS errors.

    Returns a list of ``str(ip)`` values, possibly ``[]`` on hard failure.
    """
    try:
        infos = socket.getaddrinfo(host, ALLOWED_DOWNLOAD_PORT, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for info in infos:
        try:
            sockaddr = info[4]
            ip = str(sockaddr[0])
        except (IndexError, TypeError):
            continue
        if ip in seen:
            continue
        seen.add(ip)
        out.append(ip)
    return out


def _run_resolver(resolver: Resolver, host: str) -> list[str]:
    """Invoke an injected resolver and fail-closed on errors.

    Mirrors ``_resolve_safely`` but trusts the caller to supply the IPs.
    Any exception is converted to ``[]`` so the downstream ``is_global``
    check raises ``NetworkSafetyError`` with the correct wording.
    """
    try:
        result = list(resolver(host))
    except (socket.gaierror, UnicodeError, OSError, ValueError, TypeError):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for ip in result:
        if not isinstance(ip, str) or not ip:
            continue
        if ip in seen:
            continue
        seen.add(ip)
        out.append(ip)
    return out


_DEFAULT_HTTPS_PORT = 443
_DEFAULT_HTTP_PORT = 80


def _normalize_url(url: str) -> str:
    """Return a canonical string for redirect-cycle comparison.

    Lowercases scheme + host, drops default ports (``443`` for HTTPS,
    ``80`` for HTTP), strips the fragment, and collapses dot-segments via
    ``posixpath.normpath`` so equivalent URLs cannot bypass direct
    string equality.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    scheme = (parsed.scheme or "").lower()
    hostname = (parsed.hostname or "").lower()
    default_port = _DEFAULT_HTTPS_PORT if scheme == "https" else _DEFAULT_HTTP_PORT
    netloc = f"{hostname}:{parsed.port}" if parsed.port is not None and parsed.port != default_port else hostname
    raw_path = parsed.path or "/"
    normalized_path = posixpath.normpath(raw_path)
    # ``posixpath.normpath("/")`` returns ``"/"`` and ``normpath("")``
    # returns ``"."`` — re-anchor empty paths so they remain absolute.
    if not normalized_path.startswith("/"):
        normalized_path = "/" + normalized_path
    return urlunparse((scheme, netloc, normalized_path, parsed.params, parsed.query, ""))


def _coerce_response(response: httpx.Response, *, path: str | None) -> ApiResponse:
    content_type = response.headers.get("content-type", "application/octet-stream").split(";", 1)[0].strip().lower()
    if response.status_code >= 400:
        payload = _try_json(response)
        request_id = (
            response.headers.get("x-request-id")
            or response.headers.get("request-id")
            or response.headers.get("X-Request-Id")
        )
        raise ApiError(
            response.status_code,
            _error_message(payload, response.text),
            payload=payload,
            request_id=request_id,
        )
    body: bytes | None = None
    parsed: Any = None
    if content_type.startswith(
        ("image/", "audio/", "video/", "application/octet-stream")
    ) and not content_type.startswith("application/json"):
        body = response.content
    else:
        try:
            parsed = response.json()
        except ValueError:
            body = response.content
    sha = hashlib.sha256(response.content).hexdigest() if response.content else None
    return ApiResponse(
        status_code=response.status_code,
        content_type=content_type,
        headers=dict(response.headers),
        content=body,
        json_data=parsed,
        sha256=sha,
        path=path,
    )


def _try_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _error_message(payload: Any, text: str) -> str:
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        return str(payload["error"].get("message") or payload["error"])
    if isinstance(payload, dict) and "message" in payload:
        return str(payload["message"])
    return (text or "")[:512]
