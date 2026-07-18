from __future__ import annotations

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
