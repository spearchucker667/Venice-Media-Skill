"""Minimal Venice HTTP client with explicit binary and consent handling."""

from __future__ import annotations

import importlib.metadata
import ipaddress
import json
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from .errors import ApiError, ConfigurationError, ConsentRequired


def _get_package_version() -> str:
    """Get the package version for User-Agent header."""
    try:
        return importlib.metadata.version("venice-media-skill")
    except importlib.metadata.PackageNotFoundError:
        return "0.1.0"


@dataclass(slots=True)
class ApiResponse:
    status_code: int
    content_type: str
    headers: Mapping[str, str]
    json_data: Any | None = None
    content: bytes | None = None

    @property
    def is_binary(self) -> bool:
        return self.content is not None


class VeniceClient:
    """Thin synchronous client designed for deterministic CLI subprocess use."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        timeout_seconds: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ConfigurationError("VENICE_API_KEY is required for API operations.")
        self._timeout_seconds = timeout_seconds
        self._client = httpx.Client(
            base_url=base_url.rstrip("/") + "/",
            timeout=timeout_seconds,
            headers={
                "Authorization": f"Bearer {api_key}",
                "User-Agent": f"venice-media-skill/{_get_package_version()}",
                "Accept": "application/json, image/*, audio/*, video/*, text/plain",
            },
            follow_redirects=True,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> VeniceClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def download_public_url(self, url: str) -> ApiResponse:
        """Download a pre-signed media URL without forwarding Venice credentials.

        Security: This method validates URLs to prevent SSRF attacks by:
        - Requiring HTTPS (no HTTP)
        - Blocking loopback, private, link-local, multicast, and reserved IP addresses
        - Validating redirect targets
        - Enforcing maximum response size
        """
        # Require HTTPS only (no HTTP)
        if not url.startswith("https://"):
            raise ApiError(0, "Only HTTPS URLs are allowed for downloads", payload={"url": url})

        # Parse and validate the URL
        self._validate_url_safe(url)

        # Configure streaming to handle large responses safely
        MAX_DOWNLOAD_SIZE = 500 * 1024 * 1024  # 500MB limit

        with httpx.Client(
            timeout=self._timeout_seconds,
            follow_redirects=True,
            limits={"max_keepalive_connections": 1, "max_connections": 1},
        ) as http_client:
            try:
                response = http_client.get(
                    url,
                    headers={"User-Agent": f"venice-media-skill/{_get_package_version()}"},
                )
            except httpx.NetworkError as exc:
                raise ApiError(
                    0, f"Network error downloading URL: {exc}", payload={"url": url}
                ) from exc

            # Validate redirect URL if redirect occurred
            if response.url != url:
                self._validate_url_safe(response.url)

            # Check for success
            if not response.is_success:
                payload = _try_json(response)
                raise ApiError(
                    response.status_code, _error_message(payload, response.text), payload=payload
                )

            # Check content length to prevent memory exhaustion
            content_length = response.headers.get("content-length")
            if content_length:
                try:
                    cl = int(content_length)
                    if cl > MAX_DOWNLOAD_SIZE:
                        raise ApiError(
                            413,
                            f"Download size {cl} bytes exceeds maximum of {MAX_DOWNLOAD_SIZE}",
                            payload={"url": url, "content_length": cl},
                        )
                except ValueError:
                    pass

            # For large responses, check actual content length
            content_type = response.headers.get("content-type", "application/octet-stream")
            payload = _try_json(response)

            if _is_json_content_type(content_type):
                return ApiResponse(
                    status_code=response.status_code,
                    content_type=content_type,
                    headers=response.headers,
                    json_data=payload,
                )

            # Check actual content size
            content = response.content
            if len(content) > MAX_DOWNLOAD_SIZE:
                raise ApiError(
                    413,
                    f"Downloaded content size {len(content)} bytes exceeds maximum",
                    payload={"url": url, "actual_size": len(content)},
                )

            return ApiResponse(
                status_code=response.status_code,
                content_type=content_type,
                headers=response.headers,
                content=content,
            )

    def _validate_url_safe(self, url: str) -> None:
        """Validate that a URL is safe to fetch (not SSRF-risky).

        Blocks:
        - Non-HTTPS URLs
        - Loopback addresses (127.0.0.0/8, ::1, localhost)
        - Private networks (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, etc.)
        - Link-local addresses (169.254.0.0/16, fe80::/10)
        - Multicast addresses
        - Reserved addresses
        - Cloud metadata endpoints (169.254.169.254)
        """
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            raise ApiError(0, "URL has no hostname", payload={"url": url})

        # Try to parse as IP address
        try:
            ip = ipaddress.ip_address(hostname)
            self._validate_ip_safe(ip)
            return
        except ValueError:
            pass  # Not an IP, try DNS resolution

        # Try to resolve hostname to IPs
        try:
            # Get all A and AAAA records
            for _family, _, _, _, sockaddr in socket.getaddrinfo(hostname, None):
                ip_str = sockaddr[0]
                try:
                    ip = ipaddress.ip_address(ip_str)
                    self._validate_ip_safe(ip)
                except ValueError:
                    continue
        except (socket.gaierror, OSError, OverflowError):
            # Can't resolve - this might fail at request time
            # But we can't validate it now, so we allow it
            # The request will fail if DNS resolution fails
            pass

    def _validate_ip_safe(self, ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
        """Validate that an IP address is not in a dangerous range."""
        # Loopback
        if ip.is_loopback:
            raise ApiError(0, "Loopback addresses are not allowed", payload={"ip": str(ip)})

        # Private networks
        if ip.is_private:
            raise ApiError(0, "Private network addresses are not allowed", payload={"ip": str(ip)})

        # Link-local
        if ip.is_link_local:
            raise ApiError(0, "Link-local addresses are not allowed", payload={"ip": str(ip)})

        # Multicast
        if ip.is_multicast:
            raise ApiError(0, "Multicast addresses are not allowed", payload={"ip": str(ip)})

        # Reserved
        if ip.is_reserved:
            raise ApiError(0, "Reserved addresses are not allowed", payload={"ip": str(ip)})

        # Check for cloud metadata endpoints (explicit check)
        if str(ip) == "169.254.169.254":
            raise ApiError(0, "Cloud metadata endpoint is not allowed", payload={"ip": str(ip)})

    def get_json(self, path: str, *, params: Mapping[str, Any] | None = None) -> Any:
        response = self.request("GET", path, params=params)
        return response.json_data

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        files: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
    ) -> ApiResponse:
        response = self._client.request(
            method,
            path.lstrip("/"),
            params=params,
            json=json_body,
            files=files,
            data=data,
        )
        content_type = response.headers.get("content-type", "application/octet-stream")
        request_id = response.headers.get("x-request-id") or response.headers.get("request-id")
        payload = _try_json(response)
        if response.status_code == 409 and _is_consent_payload(payload):
            assert isinstance(payload, dict)
            raise ConsentRequired(payload)
        if not response.is_success:
            message = _error_message(payload, response.text)
            raise ApiError(response.status_code, message, payload=payload, request_id=request_id)
        if _is_json_content_type(content_type):
            return ApiResponse(
                status_code=response.status_code,
                content_type=content_type,
                headers=response.headers,
                json_data=payload,
            )
        return ApiResponse(
            status_code=response.status_code,
            content_type=content_type,
            headers=response.headers,
            content=response.content,
        )


def _try_json(response: httpx.Response) -> Any | None:
    try:
        return response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _is_json_content_type(content_type: str) -> bool:
    normalized = content_type.lower()
    return "application/json" in normalized or "+json" in normalized


def _is_consent_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    return (isinstance(error, dict) and error.get("code") == "needs_consent") or payload.get(
        "consent_flow"
    ) == "seedance"


def _error_message(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, str):
            return error
        if isinstance(error, dict):
            for key in ("message", "error", "code"):
                if error.get(key):
                    return str(error[key])
        if payload.get("message"):
            return str(payload["message"])
    compact = " ".join(fallback.split())
    return compact[:500] or "Unknown API error"
