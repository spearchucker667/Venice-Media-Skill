"""Per-operation payload builders, normalization, and reserved-key gating.

This module is the single authority for converting a validated
:class:`~venice_media_skill.request.MediaRequest` into a strictly shaped
provider body. Everything that the Venice client will see on the wire for
each operation flows through exactly one builder here, ensuring:

* No reserved/transport-control keys ever appear in a provider body.
* ``consents.seedance`` is never added from arbitrary ``parameters``.
* Quote and queue payloads are derived from the same canonical payload for
  the same logical request.
* Every builder produces a stable hash so consent challenges and quote
  approvals are bound to the exact bytes the provider will receive.

The builders intentionally raise typed errors rather than returning ``None``
when the caller asks for something disallowed. The runner never ``.get()``
around them; it consumes the typed result.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .errors import ReservedParameterError
from .request import MediaRequest
from .reserved import RESERVED_PARAMETERS, RESERVED_PROVIDER_KEYS, RESERVED_TOP_LEVEL_KEYS
from .util import normalize_media_input, stable_json

# Re-exported for any caller that already imports from this module.
__all__ = [
    "RESERVED_PARAMETERS",
    "RESERVED_PROVIDER_KEYS",
    "RESERVED_TOP_LEVEL_KEYS",
    "CanonicalPayload",
    "assert_no_reserved_parameters",
    "build_audio_queue",
    "build_audio_quote",
    "build_image_background_remove",
    "build_image_edit",
    "build_image_generate",
    "build_image_multi_edit",
    "build_image_upscale",
    "build_transcribe",
    "build_tts",
    "build_video_queue",
    "build_video_quote",
    "sha256_hex",
]

# Reserved keys live in :mod:`venice_media_skill.reserved` so that the
# request/manifest validator and the payload builder can both import them
# without forming a circular dependency.


@dataclass(slots=True, frozen=True)
class CanonicalPayload:
    """Operation-specific provider body and its canonical hash.

    ``payload`` is the dictionary that will be sent verbatim (after JSON
    serialization) to Venice. ``hash`` is the SHA-256 of the canonical
    serialization — quote, queue, and consent all bind to it.
    """

    operation: str
    endpoint: str
    payload: Mapping[str, Any]
    hash: str
    input_hashes: tuple[str, ...]


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    return sha256_hex(stable_json(payload).encode("utf-8"))


def assert_no_reserved_parameters(parameters: Mapping[str, Any]) -> None:
    """Reject any reserved or transport-control key inside ``parameters``."""
    for key in parameters:
        if key in RESERVED_PARAMETERS:
            raise ReservedParameterError(key)


def _input_hashes(request: MediaRequest) -> tuple[str, ...]:
    hashes: list[str] = []
    for raw in request.inputs.values():
        if isinstance(raw, str):
            hashes.append(_sha256_text(_canonicalize_input(raw)))
        elif isinstance(raw, list):
            for item in raw:
                if isinstance(item, str):
                    hashes.append(_sha256_text(_canonicalize_input(item)))
    return tuple(hashes)


def _sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonicalize_input(value: str) -> str:
    """Return the canonical string used to hash an input media reference.

    URLs are taken verbatim after stripping fragments; data URLs are
    validated so the hash binds to the exact bytes. Local paths are
    resolved and hashed by file content to defeat path swaps.
    """
    if value.startswith("data:"):
        from .util import decode_data_url  # local import to avoid cycle

        _, blob = decode_data_url(value)
        return sha256_hex(blob)
    if value.startswith(("http://", "https://")):
        return value.split("#", 1)[0]
    from pathlib import Path

    path = Path(value).expanduser().resolve()
    if not path.is_file():
        # If the file is not present we still produce a stable hash from the
        # original reference — the runner will surface a clearer error later.
        return sha256_hex(value.encode("utf-8"))
    return sha256_hex(path.read_bytes())


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def reject_unknown_fields(mapping: Mapping[str, Any], allowed: set[str], context: str) -> None:
    unknown = set(mapping) - allowed
    if unknown:
        unknown_list = ", ".join(sorted(unknown))
        raise ReservedParameterError(f"<set of {len(unknown)} fields: {unknown_list}>", context=context)


def _copy_only(parameters: Mapping[str, Any], allowed_keys: set[str]) -> dict[str, Any]:
    """Project the caller's ``parameters`` down to a typed allowlist.

    Reserved keys are rejected at the gate; unexpected keys raise so the
    manifest author cannot accidentally feed unspecified fields into the
    API body.
    """
    assert_no_reserved_parameters(parameters)
    reject_unknown_fields(parameters, allowed_keys, context="parameters")
    return {key: parameters[key] for key in allowed_keys if key in parameters}


def build_image_generate(request: MediaRequest) -> CanonicalPayload:
    """``POST /image/generate`` - canonical provider body."""
    if request.model is None or request.prompt is None:
        raise ValueError("image.generate requires model and prompt")
    allowed = {
        "format",
        "variants",
        "negative_prompt",
        "seed",
        "style",
        "aspect_ratio",
        "resolution",
    }
    body = _copy_only(request.parameters, allowed)
    payload: dict[str, Any] = {
        "model": request.model,
        "prompt": request.prompt,
        "safe_mode": False,
        "hide_watermark": True,
    }
    payload.update(body)
    payload.setdefault("format", "webp")
    variants = payload.get("variants", 1)
    if isinstance(variants, bool) or not isinstance(variants, int) or not 1 <= variants <= 4:
        raise ValueError("parameters.variants must be an integer in [1, 4].")
    payload["variants"] = variants
    payload["return_binary"] = variants == 1
    return _wrap(request, "image.generate", "/image/generate", payload)


def build_image_edit(request: MediaRequest) -> CanonicalPayload:
    """``POST /image/edit`` - canonical provider body."""
    if request.model is None or request.prompt is None:
        raise ValueError("image.edit requires model and prompt")
    allowed = {"aspect_ratio", "resolution", "output_format", "style"}
    body = _copy_only(request.parameters, allowed)
    image = _require_single_string_input(request, "image")
    payload: dict[str, Any] = {
        "model": request.model,
        "prompt": request.prompt,
        "image": normalize_media_input(image),
        "safe_mode": False,
    }
    if "output_format" in body:
        payload["output_format"] = body["output_format"]
    else:
        payload.setdefault("output_format", "png")
    payload.update({k: v for k, v in body.items() if k != "output_format"})
    return _wrap(request, "image.edit", "/image/edit", payload)


def build_image_multi_edit(request: MediaRequest) -> CanonicalPayload:
    """``POST /image/multi-edit`` - canonical provider body."""
    if request.model is None or request.prompt is None:
        raise ValueError("image.multi_edit requires model and prompt")
    allowed = {"aspect_ratio", "resolution", "output_format", "quality"}
    body = _copy_only(request.parameters, allowed)
    images = _require_string_list_input(request, "images", min_items=1, max_items=3)
    payload: dict[str, Any] = {
        "modelId": request.model,
        "prompt": request.prompt,
        "images": [normalize_media_input(item) for item in images],
        "safe_mode": False,
    }
    payload.setdefault("output_format", "png")
    payload.update(body)
    return _wrap(request, "image.multi_edit", "/image/multi-edit", payload)


def build_image_upscale(request: MediaRequest) -> CanonicalPayload:
    """``POST /image/upscale`` - canonical provider body."""
    allowed = {"creativity", "scale"}
    body = _copy_only(request.parameters, allowed)
    image = _require_single_string_input(request, "image")
    payload: dict[str, Any] = {
        "image": normalize_media_input(image),
    }
    payload.setdefault("scale", 2)
    payload["scale"] = _check_scale(body.get("scale", 2))
    if "creativity" in body:
        payload["creativity"] = _check_creativity(float(body["creativity"]))
    return _wrap(request, "image.upscale", "/image/upscale", payload)


def build_image_background_remove(request: MediaRequest) -> CanonicalPayload:
    """``POST /image/background-remove`` - canonical provider body."""
    image = _require_single_string_input(request, "image")
    normalized = normalize_media_input(image)
    key = "image_url" if normalized.startswith(("http://", "https://")) else "image"
    payload: dict[str, Any] = {key: normalized}
    return _wrap(request, "image.background_remove", "/image/background-remove", payload)


def build_tts(request: MediaRequest) -> CanonicalPayload:
    """``POST /audio/speech`` - canonical provider body."""
    if request.model is None or request.prompt is None:
        raise ValueError("audio.tts requires model and prompt")
    allowed = {"voice", "response_format", "speed"}
    body = _copy_only(request.parameters, allowed)
    payload: dict[str, Any] = {
        "model": request.model,
        "input": request.prompt,
    }
    payload.setdefault("response_format", "mp3")
    payload.setdefault("speed", 1.0)
    payload.update(body)
    return _wrap(request, "audio.tts", "/audio/speech", payload)


def build_transcribe(request: MediaRequest) -> CanonicalPayload:
    """``POST /audio/transcriptions`` - canonical multipart metadata.

    The binary file is supplied separately by the runner; only the
    structured fields and their hash are encoded in :class:`CanonicalPayload`.
    """
    if request.model is None:
        raise ValueError("audio.transcribe requires model")
    allowed = {"response_format", "timestamps", "language"}
    body = _copy_only(request.parameters, allowed)
    payload: dict[str, Any] = {"model": request.model}
    payload["response_format"] = str(body.get("response_format", "json"))
    payload["timestamps"] = _strict_bool_string(body.get("timestamps", False))
    if "language" in body:
        payload["language"] = str(body["language"])
    return _wrap(request, "audio.transcribe", "/audio/transcriptions", payload)


def build_video_queue(request: MediaRequest) -> CanonicalPayload:
    """``POST /video/queue`` - canonical provider body for video."""
    if request.model is None or request.prompt is None:
        raise ValueError("video.generate requires model and prompt")
    allowed = {
        "duration",
        "aspect_ratio",
        "resolution",
        "upscale_factor",
        "audio",
        "reference_video_total_duration",
        "seed",
        "negative_prompt",
        "style",
    }
    body = _copy_only(request.parameters, allowed)
    payload: dict[str, Any] = {"model": request.model, "prompt": request.prompt}
    payload.update(body)
    mapping = {
        "image": "image_url",
        "end_image": "end_image_url",
        "audio": "audio_url",
        "video": "video_url",
    }
    for source, target in mapping.items():
        value = request.inputs.get(source)
        if isinstance(value, str):
            payload[target] = normalize_media_input(value)
    list_mapping = {
        "reference_images": "reference_image_urls",
        "reference_videos": "reference_video_urls",
        "reference_audios": "reference_audio_urls",
        "scene_images": "scene_image_urls",
    }
    for source, target in list_mapping.items():
        values = request.inputs.get(source)
        if isinstance(values, list) and all(isinstance(item, str) for item in values):
            payload[target] = [normalize_media_input(item) for item in values]
    if "elements" in request.inputs:
        # ``elements`` is a provider control surface — reject unless the
        # caller explicitly supplies through ``inputs`` (validated) and we
        # include it as a typed value rather than a free-form parameters key.
        elements = request.inputs["elements"]
        if isinstance(elements, list):
            payload["elements"] = elements
        else:
            raise ValueError("inputs.elements must be a list when provided.")
    return _wrap(request, "video.generate", "/video/queue", payload)


def build_video_quote(request: MediaRequest) -> CanonicalPayload:
    """``POST /video/quote`` - canonical provider body.

    Derived from the same canonical queue payload so the quote response
    cannot disagree with what gets queued. The quote body is a subset of
    the queue body (no reference media), but we must keep the same hash
    for the quote/queue gate. We compute the hash from the full queue
    payload and return a CanonicalPayload with the quote body but the
    queue payload's hash.
    """
    queue_canonical = build_video_queue(request)
    payload = dict(queue_canonical.payload)
    extras = _copy_only(request.parameters, {"model", "duration"})
    # Quote does not need reference media; strip image/audio/video urls.
    for key in (
        "image_url",
        "end_image_url",
        "audio_url",
        "video_url",
        "reference_image_urls",
        "reference_video_urls",
        "reference_audio_urls",
        "scene_image_urls",
        "elements",
    ):
        payload.pop(key, None)
    payload["model"] = extras.get("model", request.model)
    if "duration" in extras:
        payload["duration"] = extras["duration"]
    # Return quote body but with the queue payload's hash so the gate passes.
    return CanonicalPayload(
        operation=queue_canonical.operation,
        endpoint="/video/quote",
        payload=payload,
        hash=queue_canonical.hash,
        input_hashes=queue_canonical.input_hashes,
    )


def build_audio_queue(request: MediaRequest) -> CanonicalPayload:
    """``POST /audio/queue`` - canonical provider body for audio generation."""
    if request.model is None or request.prompt is None:
        raise ValueError("audio.generate requires model and prompt")
    allowed = {
        "lyrics_prompt",
        "duration_seconds",
        "language_code",
        "voice",
        "force_instrumental",
        "lyrics_optimizer",
        "speed",
    }
    body = _copy_only(request.parameters, allowed)
    payload: dict[str, Any] = {"model": request.model, "prompt": request.prompt}
    payload.update(body)
    return _wrap(request, "audio.generate", "/audio/queue", payload)


def build_audio_quote(request: MediaRequest) -> CanonicalPayload:
    """``POST /audio/quote`` - canonical provider body for audio quoting.

    Quote schema only accepts: model, duration_seconds, character_count.
    """
    extras = _copy_only(request.parameters, {"model", "duration_seconds", "character_count"})
    body = build_audio_queue(request)
    # Quote body must only contain quote schema fields
    quote_payload = {"model": extras.get("model", request.model)}
    if "duration_seconds" in extras:
        quote_payload["duration_seconds"] = extras["duration_seconds"]
    if "character_count" in extras:
        quote_payload["character_count"] = extras["character_count"]
    # Return quote body but with queue payload's hash for the gate
    return CanonicalPayload(
        operation=body.operation,
        endpoint="/audio/quote",
        payload=quote_payload,
        hash=body.hash,
        input_hashes=body.input_hashes,
    )


def append_consents(seedance: Mapping[str, Any], into: dict[str, Any]) -> None:
    """Attach a fully-formed Seedance consent object to a queue payload.

    Only the consent challenge state-machine (see ``consent.py``) is allowed
    to call this. It is intentionally not exposed on the builder module's
    surface for callers — the runner is the only consumer.
    """
    into["consents"] = dict(seedance)


def _wrap(request: MediaRequest, operation: str, endpoint: str, payload: dict[str, Any]) -> CanonicalPayload:
    return CanonicalPayload(
        operation=operation,
        endpoint=endpoint,
        payload=payload,
        hash=_canonical_hash(payload),
        input_hashes=_input_hashes(request),
    )


def _require_single_string_input(request: MediaRequest, key: str) -> str:
    value = request.inputs.get(key)
    if isinstance(value, str):
        return value
    alt_key = f"{key}s"
    alt = request.inputs.get(alt_key)
    if isinstance(alt, list) and alt and isinstance(alt[0], str):
        return alt[0]
    raise ValueError(f"{request.operation} requires a string inputs.{key}.")


def _require_string_list_input(request: MediaRequest, key: str, *, min_items: int, max_items: int) -> list[str]:
    value = request.inputs.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{request.operation} requires inputs.{key} as a list of strings.")
    if not min_items <= len(value) <= max_items:
        raise ValueError(f"{request.operation} requires inputs.{key} with {min_items}-{max_items} items.")
    return value


def _check_scale(value: Any) -> int:
    if value not in (2, 4):
        raise ValueError("image.upscale parameters.scale must be 2 or 4.")
    return int(value)


def _check_creativity(value: float) -> float:
    if not 0.0 <= value <= 0.02:
        raise ValueError("image.upscale parameters.creativity must be in [0.0, 0.02].")
    return float(value)


def _strict_bool_string(value: Any) -> str:
    """Convert ``value`` to ``"true"`` / ``"false"`` strictly.

    Non-empty strings are intentionally treated as malformed rather than
    python-truthy: ``"false"`` would coerce to ``True`` under ``bool()``.
    Allow exactly ``True``, ``False``, or the lowercase literals.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str) and value in {"true", "false"}:
        return value
    raise ValueError("parameters.timestamps must be a boolean or the literal 'true'/'false'.")


def dump(payload: CanonicalPayload) -> str:
    return json.dumps(payload.payload, sort_keys=True, separators=(",", ":"))
