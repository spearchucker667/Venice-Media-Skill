"""Minimal Venice HTTP client with explicit binary and consent handling."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from .errors import ApiError, ConfigurationError, ConsentRequired


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
                "User-Agent": "venice-media-skill/0.1.0",
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
        """Download a pre-signed media URL without forwarding Venice credentials."""
        if not url.startswith(("https://", "http://")):
            raise ApiError(0, "Refusing a non-HTTP download URL", payload={"url": url})
        response = httpx.get(
            url,
            timeout=self._timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "venice-media-skill/0.1.0"},
        )
        content_type = response.headers.get("content-type", "application/octet-stream")
        payload = _try_json(response)
        if not response.is_success:
            raise ApiError(
                response.status_code, _error_message(payload, response.text), payload=payload
            )
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
