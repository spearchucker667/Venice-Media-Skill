from __future__ import annotations

import jsonschema
import pytest

from venice_media_skill.errors import RequestValidationError
from venice_media_skill.request import MediaRequest, request_json_schema


def test_valid_image_request() -> None:
    request = MediaRequest.from_mapping(
        {
            "operation": "image.generate",
            "model": "image-model",
            "prompt": "sunset",
            "parameters": {"variants": 1},
        }
    )
    assert request.operation == "image.generate"
    assert request.execution.wait is True
    assert request.to_dict()["version"] == "1"


def test_image_requires_model() -> None:
    with pytest.raises(RequestValidationError, match="requires a model"):
        MediaRequest.from_mapping({"operation": "image.generate", "prompt": "sunset"})


def test_invalid_variants_rejected() -> None:
    with pytest.raises(RequestValidationError, match="variants"):
        MediaRequest.from_mapping(
            {
                "operation": "image.generate",
                "model": "image-model",
                "prompt": "sunset",
                "parameters": {"variants": 5},
            }
        )


def test_seedance_attestation_requires_media() -> None:
    with pytest.raises(RequestValidationError, match="seedance"):
        MediaRequest.from_mapping(
            {
                "operation": "video.generate",
                "model": "seedance-2-0-image-to-video",
                "prompt": "animate",
                "parameters": {"duration": "5s"},
                "attestations": {"seedance_face_consent": True},
            }
        )


def test_schema_contains_operations() -> None:
    schema = request_json_schema()
    operations = schema["properties"]["operation"]["enum"]
    assert "image.generate" in operations
    assert "video.retrieve" in operations


def _video_request(inputs: object) -> dict[str, object]:
    return {
        "operation": "video.generate",
        "model": "video-model",
        "prompt": "animate",
        "parameters": {"duration": "5s"},
        "inputs": inputs,
    }


@pytest.mark.parametrize(
    "inputs",
    [
        {"image": 123},
        {"image": ""},
        {"reference_images": [123]},
        {"reference_images": ["https://cdn.venice.ai/image.png"] * 10},
        {"reference_videos": ["https://cdn.venice.ai/video.mp4"] * 4},
        {"reference_audios": ["https://cdn.venice.ai/audio.mp3"] * 4, "image": "image.png"},
        {"scene_images": ["https://cdn.venice.ai/scene.png"] * 5},
        {"elements": ["not-an-object"]},
        {"elements": [{}] * 5},
        {"elements": [{"reference_image_urls": ["image.png"] * 4}]},
        {"reference_audios": ["audio.mp3"]},
    ],
)
def test_video_input_schema_and_runtime_reject_same_invalid_shapes(inputs: object) -> None:
    payload = _video_request(inputs)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, request_json_schema())
    with pytest.raises(RequestValidationError):
        MediaRequest.from_mapping(payload)


def test_video_input_schema_and_runtime_accept_reference_workflow() -> None:
    payload = _video_request(
        {
            "reference_images": ["https://cdn.venice.ai/image.png"],
            "reference_videos": ["https://cdn.venice.ai/video.mp4"],
            "reference_audios": ["https://cdn.venice.ai/audio.mp3"],
            "scene_images": ["https://cdn.venice.ai/scene.png"],
            "elements": [
                {
                    "frontal_image_url": "https://cdn.venice.ai/front.png",
                    "reference_image_urls": ["https://cdn.venice.ai/reference.png"],
                    "video_url": "https://cdn.venice.ai/element.mp4",
                }
            ],
        }
    )
    jsonschema.validate(payload, request_json_schema())
    request = MediaRequest.from_mapping(payload)
    assert request.inputs == payload["inputs"]
