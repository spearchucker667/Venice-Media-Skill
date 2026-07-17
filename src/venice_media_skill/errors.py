"""Typed failures exposed by the bridge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class VeniceMediaError(RuntimeError):
    """Base class for expected bridge failures."""


class ConfigurationError(VeniceMediaError):
    """Invalid or missing local configuration."""


class RequestValidationError(VeniceMediaError):
    """A request manifest is malformed or unsupported."""


class OutputError(VeniceMediaError):
    """Generated media could not be decoded or persisted."""


@dataclass(slots=True)
class ApiError(VeniceMediaError):
    """A non-success response from the Venice API."""

    status_code: int
    message: str
    payload: Any = None
    request_id: str | None = None

    def __str__(self) -> str:
        suffix = f" (request_id={self.request_id})" if self.request_id else ""
        return f"Venice API returned HTTP {self.status_code}: {self.message}{suffix}"


@dataclass(slots=True)
class ConsentRequired(VeniceMediaError):
    """Seedance detected face media and requires an explicit legal attestation."""

    payload: dict[str, Any]

    def __str__(self) -> str:
        return "Seedance face-media consent is required before this request can run."
