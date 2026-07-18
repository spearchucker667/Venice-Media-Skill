"""Model-aware planning questions for host agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from .catalog import ModelCatalog

MODELLESS_OPERATIONS = {"image.upscale", "image.background_remove"}

_OPERATION_MODEL_TYPE = {
    "image.generate": "image",
    "image.edit": "inpaint",
    "image.multi_edit": "inpaint",
    "image.upscale": "upscale",
    "image.background_remove": "upscale",
    "video.generate": "video",
    "audio.generate": "music",
    "audio.tts": "tts",
    "audio.transcribe": "asr",
}


@dataclass(slots=True)
class Planner:
    catalog: ModelCatalog | None

    def plan(
        self,
        operation: str,
        *,
        prompt: str | None = None,
        model: str | None = None,
        refresh_models: bool = False,
    ) -> dict[str, Any]:
        model_type = _OPERATION_MODEL_TYPE.get(operation)
        if model_type is None:
            raise ValueError(f"Unsupported operation: {operation}")
        if operation in MODELLESS_OPERATIONS:
            model_less_questions = _questions_for_model(operation, {}, prompt=prompt)
            return {
                "schema_version": 1,
                "operation": operation,
                "prompt_received": bool(prompt),
                "selected_model": None,
                "questions": model_less_questions,
                "defaults": _defaults_for_operation(operation),
                "next_step": (
                    "Collect missing answers, create a request manifest without a model field, "
                    "then run venice-media run <manifest>."
                ),
            }
        if self.catalog is None:
            raise ValueError(f"Model catalog is required for operation: {operation}")
        models = self.catalog.list(model_type, refresh=refresh_models)
        selected = self.catalog.get(model, model_type) if model else None
        if model is not None:
            if selected is None:
                raise ValueError(f"Requested model {model!r} is missing from the live Venice catalog.")
            selected_id = selected.get("id")
            if selected_id != model:
                raise ValueError(
                    f"Requested model {model!r} resolved unexpectedly to {selected_id!r}; refusing substitution."
                )
            selected_type = selected.get("type")
            if isinstance(selected_type, str) and selected_type != model_type:
                raise ValueError(
                    f"Requested model {model!r} is unsupported for {operation}; "
                    f"catalog type is {selected_type!r}, expected {model_type!r}."
                )
            selected_spec = _dict_value(selected, "model_spec")
            if selected_spec.get("offline") is True:
                raise ValueError(f"Requested model {model!r} is currently offline.")
        questions: list[dict[str, Any]] = []
        if selected is None:
            questions.append(
                {
                    "field": "model",
                    "required": True,
                    "question": "Which Venice model should be used?",
                    "options": [_model_option(item) for item in _rank_models(models)[:20]],
                    "note": "Model availability and constraints are loaded live from GET /models.",
                }
            )
        else:
            questions.extend(_questions_for_model(operation, selected, prompt=prompt))
        return {
            "schema_version": 1,
            "operation": operation,
            "prompt_received": bool(prompt),
            "selected_model": selected,
            "questions": questions,
            "defaults": _defaults_for_operation(operation),
            "next_step": (
                ("Select a model, then call plan again with --model to obtain model-specific questions.")
                if selected is None
                else ("Collect missing answers, create a request manifest, then run venice-media run <manifest>.")
            ),
        }


def _model_option(model: dict[str, Any]) -> dict[str, Any]:
    spec = _dict_value(model, "model_spec")
    return {
        "id": model.get("id"),
        "name": spec.get("name") or model.get("id"),
        "privacy": spec.get("privacy"),
        "beta": bool(spec.get("beta") or spec.get("betaModel")),
        "deprecated": bool(spec.get("deprecation")),
        "traits": model.get("traits", []),
        "pricing": spec.get("pricing"),
    }


def _rank_models(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(model: dict[str, Any]) -> tuple[int, int, str]:
        spec = _dict_value(model, "model_spec")
        deprecated = 1 if spec.get("deprecation") else 0
        beta = 1 if spec.get("beta") or spec.get("betaModel") else 0
        return deprecated, beta, str(model.get("id", ""))

    return sorted(models, key=key)


def _constraints_lookup(model_spec: dict[str, Any]) -> dict[str, Any]:
    """Read constraints from ``model_spec`` directly or from a nested
    ``model_spec.constraints`` for older snapshots.

    The Venice API sometimes nests constraints one level deeper. We
    prefer the canonical location and fall back transparently so older
    snapshots continue to work.
    """
    nested = _dict_value(model_spec, "constraints")
    if nested:
        return nested
    return model_spec


def _questions_for_model(
    operation: str,
    model: dict[str, Any],
    *,
    prompt: str | None,
) -> list[dict[str, Any]]:
    model_spec = _dict_value(model, "model_spec")
    constraints = _constraints_lookup(model_spec)
    questions: list[dict[str, Any]] = []
    if not prompt and operation not in {
        "image.upscale",
        "image.background_remove",
        "audio.transcribe",
    }:
        questions.append(_question("prompt", True, "What should Venice create or change?"))

    if operation == "image.generate":
        aspect_ratios = _list_value(constraints, "aspectRatios", "aspect_ratios")
        resolutions = _list_value(constraints, "resolutions")
        if aspect_ratios:
            questions.append(_question("parameters.aspect_ratio", False, "What aspect ratio?", aspect_ratios))
        else:
            divisor = constraints.get("widthHeightDivisor", 8)
            questions.append(
                _question(
                    "parameters.width",
                    False,
                    f"What width? Values should be divisible by {divisor}.",
                    default=1024,
                )
            )
            questions.append(
                _question(
                    "parameters.height",
                    False,
                    f"What height? Values should be divisible by {divisor}.",
                    default=1024,
                )
            )
        if resolutions:
            questions.append(_question("parameters.resolution", False, "What resolution tier?", resolutions))
        qualities = _list_value(constraints, "qualities")
        if qualities:
            questions.append(_question("parameters.quality", False, "What quality tier?", qualities))
        steps = constraints.get("steps")
        if isinstance(steps, dict):
            questions.append(
                _question(
                    "parameters.steps",
                    False,
                    "How many inference steps?",
                    default=steps.get("default"),
                    note=f"Maximum reported by this model: {steps.get('max')}",
                )
            )
        questions.extend(
            [
                _question(
                    "parameters.negative_prompt",
                    False,
                    "Anything the image should avoid?",
                    default="",
                ),
                _question("parameters.cfg_scale", False, "Use a custom CFG scale?", default=None),
                _question("parameters.seed", False, "Use a reproducible seed?", default=None),
                _question(
                    "parameters.variants",
                    False,
                    "How many variants (1-4)?",
                    [1, 2, 3, 4],
                    default=1,
                ),
                _question(
                    "parameters.format",
                    False,
                    "Output format?",
                    ["webp", "png", "jpeg"],
                    default="webp",
                ),
            ]
        )
    elif operation in {"image.edit", "image.multi_edit"}:
        if operation == "image.edit":
            questions.append(
                _question(
                    "inputs.image",
                    True,
                    "Which local image path or public URL should be edited?",
                )
            )
        else:
            questions.append(
                _question(
                    "inputs.images",
                    True,
                    "Which local image paths or public URLs should be edited? (1-3 images)",
                )
            )
        aspect_ratios = _list_value(constraints, "aspectRatios", "aspect_ratios")
        resolutions = _list_value(constraints, "resolutions")
        if aspect_ratios:
            questions.append(
                _question(
                    "parameters.aspect_ratio",
                    False,
                    "What output aspect ratio?",
                    aspect_ratios,
                    default="auto",
                )
            )
        if resolutions:
            questions.append(_question("parameters.resolution", False, "What output resolution?", resolutions))
        questions.append(
            _question(
                "parameters.output_format",
                False,
                "Output format?",
                ["png", "jpeg", "webp"],
                default="png",
            )
        )
    elif operation == "image.upscale":
        questions.extend(
            [
                _question("inputs.image", True, "Which local image path or public URL should be upscaled?"),
                _question("parameters.scale", False, "Upscale factor?", [2, 4], default=2),
                _question("parameters.creativity", False, "Detail creativity (0.0-0.02)?", default=0.01),
            ]
        )
    elif operation == "image.background_remove":
        questions.append(
            _question(
                "inputs.image",
                True,
                "Which local image path or public URL should have its background removed?",
            )
        )
    elif operation == "video.generate":
        durations = _list_value(constraints, "durations")
        aspect_ratios = _list_value(constraints, "aspect_ratios", "aspectRatios")
        resolutions = _list_value(constraints, "resolutions")
        if durations:
            questions.append(_question("parameters.duration", True, "Video duration?", durations))
        else:
            questions.append(_question("parameters.duration", True, "Video duration?", default="5s"))
        if aspect_ratios:
            questions.append(_question("parameters.aspect_ratio", False, "Video aspect ratio?", aspect_ratios))
        if resolutions:
            questions.append(_question("parameters.resolution", False, "Video resolution?", resolutions))
        if constraints.get("audio_configurable"):
            questions.append(
                _question(
                    "parameters.audio",
                    False,
                    "Generate audio with the video?",
                    [True, False],
                    default=True,
                )
            )
        model_type = str(constraints.get("model_type", ""))
        if "image-to-video" in model_type:
            questions.append(_question("inputs.image", True, "Which first-frame image should drive the video?"))
        questions.append(_question("parameters.negative_prompt", False, "Anything the video should avoid?", default=""))
    elif operation == "audio.tts":
        voices = _list_value(constraints, "voices") or _list_value(model_spec, "voices")
        if voices:
            questions.append(_question("parameters.voice", True, "Which voice?", voices))
        else:
            questions.append(_question("parameters.voice", True, "Which Venice voice should be used?"))
        questions.extend(
            [
                _question(
                    "parameters.response_format",
                    False,
                    "Audio format?",
                    ["mp3", "wav", "flac", "aac", "opus", "pcm"],
                    default="mp3",
                ),
                _question("parameters.speed", False, "Speech speed?", default=1.0),
            ]
        )
    elif operation == "audio.generate":
        durations = _list_value(constraints, "durations", "duration_seconds")
        if durations:
            questions.append(_question("parameters.duration_seconds", False, "Audio duration?", durations))
        if constraints.get("supports_force_instrumental"):
            questions.append(
                _question(
                    "parameters.force_instrumental",
                    False,
                    "Instrumental only?",
                    [True, False],
                    default=False,
                )
            )
        if constraints.get("supports_lyrics"):
            questions.append(
                _question(
                    "parameters.lyrics_prompt",
                    False,
                    "Provide lyrics, or leave blank for instrumental/optimizer behavior?",
                    default="",
                )
            )
    elif operation == "audio.transcribe":
        questions.append(_question("inputs.audio", True, "Which local audio file should be transcribed?"))
    return questions


def _question(
    field: str,
    required: bool,
    question: str,
    options: list[Any] | None = None,
    *,
    default: Any = None,
    note: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"field": field, "required": required, "question": question}
    if options:
        result["options"] = options
    if default is not None:
        result["default"] = default
    if note:
        result["note"] = note
    return result


def _dict_value(mapping: dict[str, Any], key: str) -> dict[str, Any]:
    value = mapping.get(key)
    if isinstance(value, dict):
        return cast(dict[str, Any], value)
    return {}


def _list_value(mapping: dict[str, Any], *keys: str) -> list[Any]:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, list):
            return value
    return []


def _defaults_for_operation(operation: str) -> dict[str, Any]:
    """Return ``{parameters: {...}, execution: {...}}`` so the host agent
    can see at a glance which fields are provider-controlled and which
    fields are runner policies. Mixing them in a flat dict is no longer
    supported.
    """
    params: dict[str, Any] = {}
    execution: dict[str, Any] = {"quote_first": True, "wait": True}
    if operation == "image.generate":
        params.update({"format": "webp", "variants": 1})
    elif operation in {"image.edit", "image.multi_edit"}:
        params.update({"output_format": "png"})
    elif operation == "video.generate":
        params["audio"] = True
    elif operation == "audio.tts":
        params.update({"response_format": "mp3", "speed": 1.0})
    elif operation == "audio.generate":
        pass
    return {"parameters": params, "execution": execution}
