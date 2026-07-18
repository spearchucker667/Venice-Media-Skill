"""Manifest schema and validation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from .errors import PayloadValidationError, RequestValidationError, ReservedParameterError
from .reserved import RESERVED_PARAMETERS

SUPPORTED_OPERATIONS: Final[frozenset[str]] = frozenset(
    {
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
)

# Top-level structural manifest fields. Anything else at the top level is
# rejected; arbitrary keys cannot pass through silently because the runner
# would otherwise invent new API behavior from typos.
ALLOWED_TOP_LEVEL_KEYS: Final[frozenset[str]] = frozenset(
    {
        "version",
        "operation",
        "model",
        "prompt",
        "parameters",
        "inputs",
        "output",
        "execution",
        "attestations",
    }
)

# Operations that do not require the caller to supply a model at parse time.
# Retrieve can infer the model from the durable job record.
MODELLESS_OPERATIONS: Final[frozenset[str]] = frozenset(
    {
        "image.upscale",
        "image.background_remove",
        "video.retrieve",
        "audio.retrieve",
    }
)


@dataclass(slots=True)
class OutputSpec:
    directory: str | None = None
    filename: str | None = None
    overwrite: bool = False
    write_metadata: bool = True


@dataclass(slots=True)
class ExecutionSpec:
    dry_run: bool = False
    quote_first: bool = True
    confirmed_cost: bool = False
    skip_quote: bool = False
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

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------

    @classmethod
    def from_file(cls, path: str | Path) -> MediaRequest:
        source = Path(path)
        if not source.is_file():
            raise RequestValidationError(f"Request manifest does not exist: {source}")
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RequestValidationError(f"Unable to parse request manifest {source}: {exc}") from exc
        return cls.from_mapping(payload)

    @classmethod
    def from_mapping(cls, payload: Any) -> MediaRequest:
        if not isinstance(payload, Mapping):
            raise RequestValidationError("Request manifest must be a JSON object.")
        _reject_unknown_top_level(payload)
        version = _required_string(payload.get("version", "1"), "version") if "version" in payload else "1"
        if version != "1":
            raise RequestValidationError(f"Unsupported request manifest version: {version}")
        operation = _required_string(payload.get("operation"), "operation")
        if operation not in SUPPORTED_OPERATIONS:
            supported = ", ".join(sorted(SUPPORTED_OPERATIONS))
            raise RequestValidationError(f"Unsupported operation {operation!r}. Supported: {supported}")
        model = _optional_string(payload.get("model"), "model")
        prompt = _optional_string(payload.get("prompt"), "prompt")
        parameters = _dict_field(payload, "parameters")
        _reject_reserved_parameters(parameters)
        inputs = _dict_field(payload, "inputs")
        _reject_unknown_inputs(inputs, operation)
        output_payload = _dict_field(payload, "output")
        _reject_unknown_keys(output_payload, OUTPUT_KEYS, "output")
        execution_payload = _dict_field(payload, "execution")
        _reject_unknown_keys(execution_payload, EXECUTION_KEYS, "execution")
        attestation_payload = _dict_field(payload, "attestations")
        _reject_unknown_keys(attestation_payload, ATTESTATION_KEYS, "attestations")
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
                overwrite=_strict_bool(output_payload.get("overwrite", False), "output.overwrite"),
                write_metadata=_strict_bool(output_payload.get("write_metadata", True), "output.write_metadata"),
            ),
            execution=ExecutionSpec(
                dry_run=_strict_bool(execution_payload.get("dry_run", False), "execution.dry_run"),
                quote_first=_strict_bool(execution_payload.get("quote_first", True), "execution.quote_first"),
                confirmed_cost=_strict_bool(execution_payload.get("confirmed_cost", False), "execution.confirmed_cost"),
                skip_quote=_strict_bool(execution_payload.get("skip_quote", False), "execution.skip_quote"),
                wait=_strict_bool(execution_payload.get("wait", True), "execution.wait"),
                poll_interval_seconds=_positive_float(
                    execution_payload.get("poll_interval_seconds", 5.0),
                    "execution.poll_interval_seconds",
                ),
                timeout_seconds=_positive_float(
                    execution_payload.get("timeout_seconds", 900.0), "execution.timeout_seconds"
                ),
                delete_remote_on_completion=_strict_bool(
                    execution_payload.get("delete_remote_on_completion", False),
                    "execution.delete_remote_on_completion",
                ),
            ),
            attestations=Attestations(
                seedance_face_consent=(
                    _strict_bool(
                        attestation_payload.get("seedance_face_consent", False),
                        "attestations.seedance_face_consent",
                    )
                    if attestation_payload
                    else False
                )
            ),
        )
        request.validate()
        return request

    # ------------------------------------------------------------------
    # validation
    # ------------------------------------------------------------------

    def validate(self) -> None:
        if self.attestations.seedance_face_consent and not self.inputs:
            raise RequestValidationError(
                "attestations.seedance_face_consent was set but no inputs are present. "
                "The boolean alone is not consent; the bridge requires an explicit "
                "approve-consent command after the provider returns a 409 challenge."
            )
        if self.operation not in MODELLESS_OPERATIONS and not self.model:
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

        _validate_parameters(self)

        if self.operation in {"image.edit", "image.multi_edit"}:
            images = self.inputs.get("images")
            image = self.inputs.get("image")
            if self.operation == "image.edit" and not (image or images):
                raise RequestValidationError("image.edit requires inputs.image or inputs.images.")
            if self.operation == "image.multi_edit" and (not isinstance(images, list) or not 1 <= len(images) <= 3):
                raise RequestValidationError("image.multi_edit requires inputs.images with 1-3 items.")
        if self.operation in {"image.upscale", "image.background_remove"} and not self.inputs.get("image"):
            raise RequestValidationError(f"{self.operation} requires inputs.image.")
        if self.operation == "video.generate" and not self.parameters.get("duration"):
            raise RequestValidationError("video.generate requires parameters.duration.")
        if self.operation in {"video.retrieve", "audio.retrieve"} and "queue_id" not in self.parameters:
            raise RequestValidationError(f"{self.operation} requires parameters.queue_id.")
        if self.operation == "audio.transcribe" and not self.inputs.get("audio"):
            raise RequestValidationError("audio.transcribe requires inputs.audio.")
        if (
            self.operation.startswith("video.generate") or self.operation.startswith("audio.generate")
        ) and self.execution.skip_quote:
            # An explicit skip_quote requires execution.skip_quote_ack=true
            # AND a stored quote approval ID. We surface this as a runtime
            # error from the runner; here we just refuse to encode skip + dry_run
            # silence: skip_quote always requires a quote_approval_id at runtime.
            pass

    # ------------------------------------------------------------------
    # serialization
    # ------------------------------------------------------------------

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
                "skip_quote": self.execution.skip_quote,
                "wait": self.execution.wait,
                "poll_interval_seconds": self.execution.poll_interval_seconds,
                "timeout_seconds": self.execution.timeout_seconds,
                "delete_remote_on_completion": self.execution.delete_remote_on_completion,
            },
            "attestations": {
                "seedance_face_consent": self.attestations.seedance_face_consent,
            },
        }


# ---------------------------------------------------------------------------
# JSON schema for documentation/validation
# ---------------------------------------------------------------------------


def request_json_schema() -> dict[str, Any]:
    reserved_list = sorted(RESERVED_PARAMETERS)

    # Build per-operation parameter schemas
    param_schemas: dict[str, dict[str, Any]] = {}
    for op, rule in _PARAM_RULES.items():
        props: dict[str, dict[str, Any]] = {}
        for key in rule.get("strings", set()):
            props[key] = {"type": "string"}
        for key in rule.get("integers", set()):
            props[key] = {"type": "integer"}
        for key in rule.get("numbers", set()):
            props[key] = {"type": "number"}
        for key in rule.get("booleans", set()):
            props[key] = {"type": "boolean"}
        props["queue_id"] = {"type": "string"}
        param_schemas[op] = {
            "type": "object",
            "additionalProperties": False,
            "properties": props,
        }

    parameter_options = [{"properties": {**{"operation": {"const": op}}, **param_schemas[op]}} for op in param_schemas]
    if not parameter_options:
        parameter_options = [{}]

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
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "not": {
                    "anyOf": [{"required": [key]} for key in reserved_list],
                },
                "oneOf": parameter_options,
            },
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
                    "quote_first": {"type": "boolean", "default": True},
                    "confirmed_cost": {"type": "boolean", "default": False},
                    "skip_quote": {"type": "boolean", "default": False},
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


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

OUTPUT_KEYS = frozenset({"directory", "filename", "overwrite", "write_metadata"})
EXECUTION_KEYS = frozenset(
    {
        "dry_run",
        "quote_first",
        "confirmed_cost",
        "skip_quote",
        "wait",
        "poll_interval_seconds",
        "timeout_seconds",
        "delete_remote_on_completion",
    }
)
ATTESTATION_KEYS = frozenset({"seedance_face_consent"})


def _required_string(value: Any, key: str) -> str:
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


def _strict_bool(value: Any, field_name: str) -> bool:
    # Reject ``int`` (including Python ``bool`` -> ``int``) and ``str``.
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        raise RequestValidationError(f"{field_name} must be a boolean, not an integer.")
    if isinstance(value, str):
        raise RequestValidationError(f"{field_name} must be a boolean, not a string.")
    raise RequestValidationError(f"{field_name} must be a boolean.")


def _positive_float(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise RequestValidationError(f"{field_name} must be a positive number.")
    return float(value)


def _reject_unknown_top_level(mapping: Mapping[str, Any]) -> None:
    unknown = set(mapping) - ALLOWED_TOP_LEVEL_KEYS
    if unknown:
        keys = ", ".join(sorted(unknown))
        raise PayloadValidationError(f"Unknown top-level manifest fields: {keys}.")


def _reject_reserved_parameters(parameters: Mapping[str, Any]) -> None:
    for key in parameters:
        if key in RESERVED_PARAMETERS:
            raise ReservedParameterError(key)


def _reject_unknown_keys(mapping: Mapping[str, Any], allowed: frozenset[str] | set[str], root: str) -> None:
    unknown = set(mapping) - allowed
    if unknown:
        keys = ", ".join(f"{root}.{k}" for k in sorted(unknown))
        raise PayloadValidationError(f"Unknown fields: {keys}.")


def _reject_unknown_inputs(inputs: Mapping[str, Any], operation: str) -> None:
    # Image operations accept only structured image inputs.
    if operation.startswith("image."):
        allowed = {"image", "images", "reference_images"}
        unknown = set(inputs) - allowed
        if unknown:
            keys = ", ".join(f"inputs.{k}" for k in sorted(unknown))
            raise PayloadValidationError(f"Unknown fields: {keys}.")
    elif operation == "audio.transcribe":
        if "audio" not in inputs:
            raise PayloadValidationError("audio.transcribe requires inputs.audio.")
        unknown = set(inputs) - {"audio"}
        if unknown:
            keys = ", ".join(f"inputs.{k}" for k in sorted(unknown))
            raise PayloadValidationError(f"Unknown fields: {keys}.")
    elif operation.startswith("video."):
        allowed = {
            "image",
            "end_image",
            "audio",
            "video",
            "reference_images",
            "reference_videos",
            "reference_audios",
            "scene_images",
            "elements",
            "queue_id",
        }
        unknown = set(inputs) - allowed
        if unknown:
            keys = ", ".join(f"inputs.{k}" for k in sorted(unknown))
            raise PayloadValidationError(f"Unknown fields: {keys}.")
    elif operation.startswith("audio."):
        allowed = {"audio", "queue_id"}
        unknown = set(inputs) - allowed
        if unknown:
            keys = ", ".join(f"inputs.{k}" for k in sorted(unknown))
            raise PayloadValidationError(f"Unknown fields: {keys}.")


_PARAM_RULES: dict[str, dict[str, set[str]]] = {
    "image.generate": {
        "strings": {"format", "negative_prompt", "style", "aspect_ratio", "resolution"},
        "integers": {"variants", "seed"},
    },
    "image.edit": {
        "strings": {"output_format", "style", "aspect_ratio", "resolution"},
        "integers": set(),
    },
    "image.multi_edit": {
        "strings": {"output_format", "aspect_ratio", "resolution"},
        "integers": set(),
    },
    "image.upscale": {
        "strings": set(),
        "integers": {"scale"},
        "numbers": {"creativity"},
    },
    "image.background_remove": {"strings": set(), "integers": set()},
    "video.generate": {
        "strings": {
            "duration",
            "aspect_ratio",
            "resolution",
            "audio",
        },
        "integers": {"upscale_factor", "seed", "reference_video_total_duration"},
    },
    "audio.generate": {
        "strings": {"lyrics_prompt", "voice", "language_code"},
        "integers": {"duration_seconds", "character_count"},
        "booleans": {"force_instrumental", "lyrics_optimizer"},
        "numbers": {"speed"},
    },
    "audio.tts": {
        "strings": {"voice", "response_format"},
        "numbers": {"speed"},
    },
    "audio.transcribe": {
        "strings": {"response_format", "language"},
        "booleans": {"timestamps"},
    },
    "video.retrieve": {"strings": set(), "integers": set()},
    "audio.retrieve": {"strings": set(), "integers": set()},
}


def _validate_parameters(request: MediaRequest) -> None:
    op = request.operation
    rule = _PARAM_RULES.get(op)
    if rule is None:
        return
    allowed: set[str] = set()
    allowed.update(rule.get("strings", set()))
    allowed.update(rule.get("integers", set()))
    allowed.update(rule.get("numbers", set()))
    allowed.update(rule.get("booleans", set()))
    allowed.add("queue_id")  # always permitted as a queue reference
    extra = set(request.parameters) - allowed
    if extra:
        keys = ", ".join(f"parameters.{k}" for k in sorted(extra))
        raise PayloadValidationError(f"Unknown fields: {keys}. See per-operation allowlist in payloads.py.")
    for key in rule.get("booleans", set()):
        if key in request.parameters and not isinstance(request.parameters[key], bool):
            raise PayloadValidationError(
                f"parameters.{key} must be a boolean, not {type(request.parameters[key]).__name__}."
            )
    for key in rule.get("strings", set()):
        if key in request.parameters and not isinstance(request.parameters[key], str):
            raise PayloadValidationError(
                f"parameters.{key} must be a string, not {type(request.parameters[key]).__name__}."
            )
    for key in rule.get("integers", set()):
        if key not in request.parameters:
            continue
        value = request.parameters[key]
        if isinstance(value, bool) or not isinstance(value, int):
            raise PayloadValidationError(f"parameters.{key} must be an integer, not {type(value).__name__}.")
        if key == "variants" and not 1 <= int(value) <= 4:
            raise PayloadValidationError("parameters.variants must be an integer from 1 through 4.")
        if key == "scale" and int(value) not in (2, 4):
            raise PayloadValidationError("parameters.scale must be 2 or 4.")
        if key == "upscale_factor" and int(value) not in (2, 4):
            raise PayloadValidationError("parameters.upscale_factor must be 2 or 4.")
    for key in rule.get("numbers", set()):
        if key not in request.parameters:
            continue
        value = request.parameters[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise PayloadValidationError(f"parameters.{key} must be a number, not {type(value).__name__}.")
        if key == "creativity" and not 0.0 <= float(value) <= 0.02:
            raise PayloadValidationError("parameters.creativity must be in [0.0, 0.02].")
