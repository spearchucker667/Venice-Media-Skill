"""Manifest schema and validation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import RequestValidationError

SUPPORTED_OPERATIONS = {
    "image.generate",
    "image.edit",
    "image.multi_edit",
    "image.upscale",
    "image.background_remove",
    "video.generate",
    "video.retrieve",
    "audio.tts",
    "audio.generate",
    "audio.retrieve",
    "audio.transcribe",
}


@dataclass(slots=True)
class OutputSpec:
    directory: str | None = None
    filename: str | None = None
    overwrite: bool = False
    write_metadata: bool = True


@dataclass(slots=True)
class ExecutionSpec:
    dry_run: bool = False
    quote_first: bool = False
    confirmed_cost: bool = False
    wait: bool = True
    poll_interval_seconds: float = 5.0
    timeout_seconds: float = 900.0
    delete_remote_on_completion: bool = False


@dataclass(slots=True)
class Attestations:
    seedance_face_consent: bool = False


@dataclass(slots=True)
class MediaRequest:
    operation: str
    model: str | None = None
    prompt: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    inputs: dict[str, Any] = field(default_factory=dict)
    output: OutputSpec = field(default_factory=OutputSpec)
    execution: ExecutionSpec = field(default_factory=ExecutionSpec)
    attestations: Attestations = field(default_factory=Attestations)
    version: str = "1"

    @classmethod
    def from_file(cls, path: str | Path) -> MediaRequest:
        source = Path(path)
        if not source.is_file():
            raise RequestValidationError(f"Request manifest does not exist: {source}")
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RequestValidationError(
                f"Unable to parse request manifest {source}: {exc}"
            ) from exc
        return cls.from_mapping(payload)

    @classmethod
    def from_mapping(cls, payload: Any) -> MediaRequest:
        if not isinstance(payload, Mapping):
            raise RequestValidationError("Request manifest must be a JSON object.")
        version = str(payload.get("version", "1"))
        if version != "1":
            raise RequestValidationError(f"Unsupported request manifest version: {version}")
        operation = _required_string(payload, "operation")
        if operation not in SUPPORTED_OPERATIONS:
            supported = ", ".join(sorted(SUPPORTED_OPERATIONS))
            raise RequestValidationError(
                f"Unsupported operation {operation!r}. Supported: {supported}"
            )
        model = _optional_string(payload.get("model"), "model")
        prompt = _optional_string(payload.get("prompt"), "prompt")
        parameters = _dict_field(payload, "parameters")
        inputs = _dict_field(payload, "inputs")
        output_payload = _dict_field(payload, "output")
        execution_payload = _dict_field(payload, "execution")
        attestation_payload = _dict_field(payload, "attestations")
        request = cls(
            version=version,
            operation=operation,
            model=model,
            prompt=prompt,
            parameters=parameters,
            inputs=inputs,
            output=OutputSpec(
                directory=_optional_string(output_payload.get("directory"), "output.directory"),
                filename=_optional_string(output_payload.get("filename"), "output.filename"),
                overwrite=_bool_value(output_payload.get("overwrite", False), "output.overwrite"),
                write_metadata=_bool_value(
                    output_payload.get("write_metadata", True), "output.write_metadata"
                ),
            ),
            execution=ExecutionSpec(
                dry_run=_bool_value(execution_payload.get("dry_run", False), "execution.dry_run"),
                quote_first=_bool_value(
                    execution_payload.get("quote_first", False), "execution.quote_first"
                ),
                confirmed_cost=_bool_value(
                    execution_payload.get("confirmed_cost", False), "execution.confirmed_cost"
                ),
                wait=_bool_value(execution_payload.get("wait", True), "execution.wait"),
                poll_interval_seconds=_positive_float(
                    execution_payload.get("poll_interval_seconds", 5.0),
                    "execution.poll_interval_seconds",
                ),
                timeout_seconds=_positive_float(
                    execution_payload.get("timeout_seconds", 900.0), "execution.timeout_seconds"
                ),
                delete_remote_on_completion=_bool_value(
                    execution_payload.get("delete_remote_on_completion", False),
                    "execution.delete_remote_on_completion",
                ),
            ),
            attestations=Attestations(
                seedance_face_consent=_bool_value(
                    attestation_payload.get("seedance_face_consent", False),
                    "attestations.seedance_face_consent",
                )
            ),
        )
        request.validate()
        return request

    def validate(self) -> None:
        requires_model = self.operation not in {"image.upscale", "image.background_remove"}
        if requires_model and not self.model:
            raise RequestValidationError(f"{self.operation} requires a model.")
        requires_prompt = self.operation in {
            "image.generate",
            "image.edit",
            "image.multi_edit",
            "video.generate",
            "audio.tts",
            "audio.generate",
        }
        if requires_prompt and not self.prompt:
            raise RequestValidationError(f"{self.operation} requires a non-empty prompt.")
        if self.operation == "image.generate":
            variants = self.parameters.get("variants", 1)
            if not isinstance(variants, int) or not 1 <= variants <= 4:
                raise RequestValidationError(
                    "parameters.variants must be an integer from 1 through 4."
                )
        if self.operation in {"image.edit", "image.multi_edit"}:
            images = self.inputs.get("images")
            image = self.inputs.get("image")
            if self.operation == "image.edit" and not (image or images):
                raise RequestValidationError("image.edit requires inputs.image or inputs.images.")
            if self.operation == "image.multi_edit" and (
                not isinstance(images, list) or not 1 <= len(images) <= 3
            ):
                raise RequestValidationError(
                    "image.multi_edit requires inputs.images with 1-3 items."
                )
        if self.operation in {"image.upscale", "image.background_remove"} and not self.inputs.get(
            "image"
        ):
            raise RequestValidationError(f"{self.operation} requires inputs.image.")
        if self.operation == "video.generate" and not self.parameters.get("duration"):
            raise RequestValidationError("video.generate requires parameters.duration.")
        if self.operation == "video.retrieve":
            _require_queue_id(self)
        if self.operation == "audio.retrieve":
            _require_queue_id(self)
        if self.operation == "audio.transcribe" and not self.inputs.get("audio"):
            raise RequestValidationError("audio.transcribe requires inputs.audio.")
        if self.attestations.seedance_face_consent and not self.inputs:
            raise RequestValidationError(
                "Seedance face consent was asserted but no input media is present. "
                "Refusing ambiguous attestation."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "operation": self.operation,
            "model": self.model,
            "prompt": self.prompt,
            "parameters": self.parameters,
            "inputs": self.inputs,
            "output": {
                "directory": self.output.directory,
                "filename": self.output.filename,
                "overwrite": self.output.overwrite,
                "write_metadata": self.output.write_metadata,
            },
            "execution": {
                "dry_run": self.execution.dry_run,
                "quote_first": self.execution.quote_first,
                "confirmed_cost": self.execution.confirmed_cost,
                "wait": self.execution.wait,
                "poll_interval_seconds": self.execution.poll_interval_seconds,
                "timeout_seconds": self.execution.timeout_seconds,
                "delete_remote_on_completion": self.execution.delete_remote_on_completion,
            },
            "attestations": {
                "seedance_face_consent": self.attestations.seedance_face_consent,
            },
        }


def request_json_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://raw.githubusercontent.com/spearchucker667/venice-media-skill/main/references/request.schema.json",
        "title": "Venice Media Skill request manifest",
        "type": "object",
        "additionalProperties": False,
        "required": ["operation"],
        "properties": {
            "version": {"const": "1", "default": "1"},
            "operation": {"type": "string", "enum": sorted(SUPPORTED_OPERATIONS)},
            "model": {"type": ["string", "null"]},
            "prompt": {"type": ["string", "null"]},
            "parameters": {"type": "object", "additionalProperties": True},
            "inputs": {"type": "object", "additionalProperties": True},
            "output": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "directory": {"type": ["string", "null"]},
                    "filename": {"type": ["string", "null"]},
                    "overwrite": {"type": "boolean", "default": False},
                    "write_metadata": {"type": "boolean", "default": True},
                },
            },
            "execution": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "dry_run": {"type": "boolean", "default": False},
                    "quote_first": {"type": "boolean", "default": False},
                    "confirmed_cost": {"type": "boolean", "default": False},
                    "wait": {"type": "boolean", "default": True},
                    "poll_interval_seconds": {"type": "number", "exclusiveMinimum": 0},
                    "timeout_seconds": {"type": "number", "exclusiveMinimum": 0},
                    "delete_remote_on_completion": {"type": "boolean", "default": False},
                },
            },
            "attestations": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"seedance_face_consent": {"type": "boolean", "default": False}},
            },
        },
    }


def _required_string(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RequestValidationError(f"{key} must be a non-empty string.")
    return value.strip()


def _optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RequestValidationError(f"{field_name} must be a string or null.")
    stripped = value.strip()
    return stripped or None


def _dict_field(mapping: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = mapping.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise RequestValidationError(f"{key} must be a JSON object.")
    return dict(value)


def _bool_value(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise RequestValidationError(f"{field_name} must be a boolean.")
    return value


def _positive_float(value: Any, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise RequestValidationError(f"{field_name} must be a positive number.")
    return float(value)


def _require_queue_id(request: MediaRequest) -> None:
    queue_id = request.parameters.get("queue_id")
    if not isinstance(queue_id, str) or not queue_id:
        raise RequestValidationError(f"{request.operation} requires parameters.queue_id.")
