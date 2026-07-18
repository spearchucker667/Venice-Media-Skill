"""Manifest schema and validation."""

from __future__ import annotations

import json
import math
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

# Exact per-operation input contracts. Each entry lists the only accepted
# ``inputs.*`` keys for that operation. Any key outside this set is rejected
# at parse time. An empty set means the operation accepts no inputs.
_PER_OPERATION_INPUTS: dict[str, set[str]] = {
    "image.generate": set(),
    "image.edit": {"image"},
    "image.multi_edit": {"images"},
    "image.upscale": {"image"},
    "image.background_remove": {"image"},
    "video.generate": {
        "image",
        "end_image",
        "audio",
        "video",
        "reference_images",
        "reference_videos",
        "reference_audios",
        "scene_images",
        "elements",
    },
    "video.retrieve": set(),
    "audio.tts": set(),
    "audio.generate": set(),
    "audio.retrieve": set(),
    "audio.transcribe": {"audio"},
}

# Inputs that are required per operation (must be present and non-empty).
_REQUIRED_INPUTS: dict[str, set[str]] = {
    "image.edit": {"image"},
    "image.multi_edit": {"images"},
    "image.upscale": {"image"},
    "image.background_remove": {"image"},
    "audio.transcribe": {"audio"},
}

# Inputs that must be a list (not a scalar) per operation.
_LIST_INPUTS: dict[str, set[str]] = {
    "image.multi_edit": {"images"},
    "video.generate": {
        "reference_images",
        "reference_videos",
        "reference_audios",
        "scene_images",
        "elements",
    },
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
            payload = json.loads(
                source.read_text(encoding="utf-8"),
                parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
            )
        except (OSError, json.JSONDecodeError, ValueError) as exc:
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

        # Input cardinality from the per-operation contracts.
        required = _REQUIRED_INPUTS.get(self.operation)
        if required:
            for key in required:
                if not self.inputs.get(key):
                    raise RequestValidationError(f"{self.operation} requires inputs.{key}.")
        if self.operation == "image.edit" and isinstance(self.inputs.get("image"), list):
            raise RequestValidationError(
                "image.edit accepts exactly one inputs.image; use image.multi_edit for multiple images."
            )
        if self.operation == "image.multi_edit":
            images = self.inputs.get("images")
            if not isinstance(images, list) or not 1 <= len(images) <= 3:
                raise RequestValidationError("image.multi_edit requires inputs.images with 1-3 items.")
        if self.operation == "video.generate" and not self.parameters.get("duration"):
            raise RequestValidationError("video.generate requires parameters.duration.")
        if self.operation in {"video.retrieve", "audio.retrieve"} and "queue_id" not in self.parameters:
            raise RequestValidationError(f"{self.operation} requires parameters.queue_id.")
        if (
            self.operation.startswith("video.generate") or self.operation.startswith("audio.generate")
        ) and self.execution.skip_quote:
            raise RequestValidationError(
                "execution.skip_quote is unsupported; paid queued generation always requires "
                "an explicit hash-bound quote approval."
            )

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
    """Generate the JSON Schema for request manifests.

    Produces a Draft 2020-12 schema that is meta-schema valid:

    - Top-level ``allOf`` carries one ``if/then`` block per operation
      so the discriminator lives at the top level (``operation``)
      rather than inside ``parameters``.
    - ``$defs.parameterShapes`` exposes each per-operation parameter
      schema once and is referenced from each ``then`` clause.
    - ``parameters.not`` rejects reserved keys at any nested level.
    """
    reserved_keys = sorted(RESERVED_PARAMETERS)

    def _build_param_shape(op: str) -> dict[str, Any]:
        rule = _PARAM_RULES.get(op, {})
        props: dict[str, dict[str, Any]] = {}
        for key in rule.get("strings", set()):
            props[key] = {"type": "string"}
        for key in rule.get("integers", set()):
            props[key] = {"type": "integer"}
        for key in rule.get("numbers", set()):
            props[key] = {"type": "number"}
        for key in rule.get("booleans", set()):
            props[key] = {"type": "boolean"}
        numeric_constraints: dict[str, dict[str, Any]] = {
            "variants": {"minimum": 1, "maximum": 4},
            "scale": {"enum": [2, 4]},
            "upscale_factor": {"enum": [1, 2, 4]},
            "width": {"exclusiveMinimum": 0, "maximum": 1280},
            "height": {"exclusiveMinimum": 0, "maximum": 1280},
            "lora_strength": {"minimum": 0, "maximum": 100},
            "seed": {"minimum": -999999999, "maximum": 999999999},
            "creativity": {"minimum": 0, "maximum": 0.02},
            "cfg_scale": {"exclusiveMinimum": 0, "maximum": 20},
            "speed": {"minimum": 0.25, "maximum": 4},
            "reference_video_total_duration": {"minimum": 0},
        }
        for key, constraints in numeric_constraints.items():
            if key in props:
                props[key].update(constraints)
        if op == "image.generate" and "variants" in props:
            props["variants"]["description"] = (
                "Canonical image count (1-4). The bridge uses binary response mode and omits the wire-level "
                "variants field for one image; counts 2-4 use JSON response mode and serialize variants."
            )
        if op in {"video.retrieve", "audio.retrieve"}:
            props["queue_id"] = {"type": "string"}
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": props,
        }

    param_shapes: dict[str, dict[str, Any]] = {op: _build_param_shape(op) for op in sorted(SUPPORTED_OPERATIONS)}

    def _input_property(key: str) -> dict[str, Any]:
        if key in {"images", "reference_images", "reference_videos", "reference_audios", "scene_images"}:
            maximum = {"images": 3, "reference_images": 9, "reference_videos": 3, "reference_audios": 3}.get(key)
            schema: dict[str, Any] = {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string", "minLength": 1},
            }
            if maximum is not None:
                schema["maxItems"] = maximum
            return schema
        if key == "elements":
            return {"type": "array", "minItems": 1, "items": {"type": "object"}}
        return {"type": "string", "minLength": 1}

    branches: list[dict[str, Any]] = []
    for op, shape in param_shapes.items():
        required = ["operation"]
        if op not in MODELLESS_OPERATIONS:
            required.append("model")
        if op in {"image.generate", "image.edit", "image.multi_edit", "video.generate", "audio.tts", "audio.generate"}:
            required.append("prompt")
        if op == "video.generate":
            shape["required"] = ["duration"]
        if op in {"video.retrieve", "audio.retrieve"}:
            shape["required"] = ["queue_id"]
        allowed_inputs = _allowed_input_names(op)
        input_properties = {key: _input_property(key) for key in sorted(allowed_inputs)}
        inputs_schema: dict[str, Any] = {
            "type": "object",
            "additionalProperties": False,
            "properties": input_properties,
        }
        op_required_inputs = _REQUIRED_INPUTS.get(op)
        if op_required_inputs:
            inputs_schema["required"] = sorted(op_required_inputs)
        branches.append(
            {
                "if": {
                    "type": "object",
                    "required": ["operation"],
                    "properties": {"operation": {"const": op}},
                },
                "then": {
                    "type": "object",
                    "required": required,
                    "properties": {
                        "model": {"type": "string", "minLength": 1},
                        "prompt": {"type": "string", "minLength": 1},
                        "parameters": shape,
                        "inputs": inputs_schema,
                    },
                },
            }
        )

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://raw.githubusercontent.com/spearchucker667/venice-media-skill/main/references/request.schema.json",
        "title": "Venice Media Skill request manifest",
        "type": "object",
        "additionalProperties": False,
        "required": ["operation"],
        "$defs": {
            "parameterShapes": param_shapes,
        },
        "allOf": branches,
        "properties": {
            "version": {"const": "1", "default": "1"},
            "operation": {"type": "string", "enum": sorted(SUPPORTED_OPERATIONS)},
            "model": {"type": ["string", "null"]},
            "prompt": {"type": ["string", "null"]},
            "parameters": {
                "type": "object",
                "not": {
                    "anyOf": [{"required": [key]} for key in reserved_keys],
                },
            },
            "inputs": {
                "type": "object",
                "additionalProperties": True,
                "description": "Operation-driven (see per-operation allowlist in payloads.py)",
            },
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
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value <= 0:
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


def _allowed_input_names(operation: str) -> set[str]:
    return _PER_OPERATION_INPUTS.get(operation, set())


def _reject_unknown_inputs(inputs: Mapping[str, Any], operation: str) -> None:
    allowed = _allowed_input_names(operation)
    unknown = set(inputs) - allowed
    if unknown:
        keys = ", ".join(f"inputs.{key}" for key in sorted(unknown))
        raise PayloadValidationError(f"Unknown fields: {keys}.")
    required = _REQUIRED_INPUTS.get(operation)
    if required:
        for key in required:
            if key not in inputs:
                raise PayloadValidationError(f"{operation} requires inputs.{key}.")
    # Validate cardinality for image operations.
    if operation == "image.edit" and isinstance(inputs.get("image"), list):
        raise PayloadValidationError(
            "image.edit accepts exactly one inputs.image; use image.multi_edit for multiple images."
        )
    if operation == "image.multi_edit" and "images" in inputs:
        images = inputs["images"]
        if not isinstance(images, list) or not images:
            raise PayloadValidationError("image.multi_edit requires inputs.images as a non-empty list.")
        if len(images) > 3:
            raise PayloadValidationError("image.multi_edit accepts at most 3 inputs.images.")
    # Validate types for list inputs.
    for key in _LIST_INPUTS.get(operation, set()):
        if key in inputs:
            if not isinstance(inputs[key], list):
                raise PayloadValidationError(f"inputs.{key} must be a list for {operation}.")
            if operation == "image.multi_edit" and not all(isinstance(item, str) for item in inputs[key]):
                raise PayloadValidationError(f"inputs.{key} must be a list of strings for {operation}.")


_PARAM_RULES: dict[str, dict[str, set[str]]] = {
    "image.generate": {
        "strings": {
            "format",
            "negative_prompt",
            "style_preset",
            "aspect_ratio",
            "resolution",
            "quality",
        },
        "integers": {
            "variants",
            "seed",
            "width",
            "height",
            "lora_strength",
            "steps",
        },
        "numbers": {"cfg_scale"},
        "booleans": {
            "embed_exif_metadata",
            "enable_web_search",
            "disable_prompt_optimization_thinking",
        },
    },
    "image.edit": {
        "strings": {"output_format", "aspect_ratio", "resolution"},
        "integers": set(),
        "booleans": set(),
    },
    "image.multi_edit": {
        "strings": {"output_format", "aspect_ratio", "resolution", "quality"},
        "integers": set(),
        "booleans": set(),
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
            "negative_prompt",
        },
        "integers": {"upscale_factor"},
        "numbers": {"reference_video_total_duration"},
        "booleans": {"audio"},
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


def allowed_parameter_names(operation: str) -> set[str]:
    """Return the canonical manifest parameter allowlist for an operation."""
    rule = _PARAM_RULES.get(operation, {})
    allowed: set[str] = set()
    for kind in ("strings", "integers", "numbers", "booleans"):
        allowed.update(rule.get(kind, set()))
    if operation in {"video.retrieve", "audio.retrieve"}:
        allowed.add("queue_id")
    return allowed


def _validate_parameters(request: MediaRequest) -> None:
    op = request.operation
    rule = _PARAM_RULES.get(op)
    if rule is None:
        return
    allowed = allowed_parameter_names(op)
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
        if key == "upscale_factor" and int(value) not in (1, 2, 4):
            raise PayloadValidationError("parameters.upscale_factor must be 1, 2, or 4.")
        if key in {"width", "height"} and not 0 < int(value) <= 1280:
            raise PayloadValidationError(f"parameters.{key} must be an integer in (0, 1280].")
        if key == "lora_strength" and not 0 <= int(value) <= 100:
            raise PayloadValidationError("parameters.lora_strength must be in [0, 100].")
        if key == "seed" and not -999999999 <= int(value) <= 999999999:
            raise PayloadValidationError("parameters.seed must be in [-999999999, 999999999].")
    for key in rule.get("numbers", set()):
        if key not in request.parameters:
            continue
        value = request.parameters[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise PayloadValidationError(f"parameters.{key} must be a number, not {type(value).__name__}.")
        if key == "creativity" and not 0.0 <= float(value) <= 0.02:
            raise PayloadValidationError("parameters.creativity must be in [0.0, 0.02].")
        if key == "cfg_scale" and not 0 < float(value) <= 20:
            raise PayloadValidationError("parameters.cfg_scale must be in (0, 20].")
        if key == "speed" and not 0.25 <= float(value) <= 4:
            raise PayloadValidationError("parameters.speed must be in [0.25, 4].")
        if key == "reference_video_total_duration" and float(value) < 0:
            raise PayloadValidationError("parameters.reference_video_total_duration must be non-negative.")
