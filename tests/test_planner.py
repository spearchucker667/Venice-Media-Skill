from __future__ import annotations

from typing import Any

import pytest

from venice_media_skill.planner import Planner


class FakeCatalog:
    def __init__(self, model: dict[str, Any]) -> None:
        self.model = model

    def list(self, _model_type: str, *, refresh: bool = False) -> list[dict[str, Any]]:
        return [self.model]

    def get(
        self, model_id: str | None, _model_type: str = "all", *, refresh: bool = False
    ) -> dict[str, Any] | None:
        return self.model if model_id == self.model["id"] else None


def model(model_id: str, constraints: dict[str, Any], **spec: Any) -> dict[str, Any]:
    return {"id": model_id, "model_spec": {"constraints": constraints, **spec}}


def fields(result: dict[str, Any]) -> set[str]:
    return {question["field"] for question in result["questions"]}


def test_plan_without_model_requests_model_selection() -> None:
    catalog = FakeCatalog(model("image-1", {}, privacy="private"))
    result = Planner(catalog).plan("image.generate", prompt="sunset")  # type: ignore[arg-type]
    assert result["questions"][0]["field"] == "model"
    assert result["questions"][0]["options"][0]["privacy"] == "private"


def test_image_plan_uses_model_constraints() -> None:
    catalog = FakeCatalog(
        model(
            "image-1",
            {
                "aspectRatios": ["1:1", "16:9"],
                "resolutions": ["1K", "2K"],
                "qualities": ["medium", "high"],
                "steps": {"default": 20, "max": 40},
            },
        )
    )
    result = Planner(catalog).plan(  # type: ignore[arg-type]
        "image.generate", model="image-1", prompt="sunset"
    )
    assert {
        "parameters.aspect_ratio",
        "parameters.resolution",
        "parameters.quality",
        "parameters.steps",
        "parameters.negative_prompt",
        "parameters.cfg_scale",
        "parameters.seed",
        "parameters.variants",
        "parameters.format",
    }.issubset(fields(result))
    assert "parameters.width_height" not in fields(result)
    assert result["defaults"]["safe_mode"] is False
    assert result["defaults"]["hide_watermark"] is True


def test_image_plan_falls_back_to_width_height() -> None:
    catalog = FakeCatalog(model("pixel-image", {"widthHeightDivisor": 16}))
    result = Planner(catalog).plan(  # type: ignore[arg-type]
        "image.generate", model="pixel-image", prompt="sunset"
    )
    width_question = next(
        question
        for question in result["questions"]
        if question["field"] == "parameters.width_height"
    )
    assert "16" in width_question["question"]


def test_edit_upscale_and_background_plans() -> None:
    edit_catalog = FakeCatalog(
        model("edit-1", {"aspectRatios": ["auto", "1:1"], "resolutions": ["1K"]})
    )
    edit = Planner(edit_catalog).plan(  # type: ignore[arg-type]
        "image.edit", model="edit-1", prompt="remove tree"
    )
    assert {"inputs.images", "parameters.aspect_ratio", "parameters.resolution"}.issubset(
        fields(edit)
    )

    upscale = Planner(None).plan("image.upscale")
    assert {"inputs.image", "parameters.scale", "parameters.creativity"}.issubset(fields(upscale))
    assert upscale["selected_model"] is None

    background = Planner(None).plan("image.background_remove")
    assert fields(background) == {"inputs.image"}
    assert background["selected_model"] is None


def test_video_plan_uses_duration_resolution_audio_and_image_input() -> None:
    catalog = FakeCatalog(
        model(
            "video-1",
            {
                "durations": ["5s", "10s"],
                "aspect_ratios": ["16:9", "9:16"],
                "resolutions": ["720p", "1080p"],
                "audio_configurable": True,
                "model_type": "image-to-video",
            },
        )
    )
    result = Planner(catalog).plan(  # type: ignore[arg-type]
        "video.generate", model="video-1", prompt="animate"
    )
    assert {
        "parameters.duration",
        "parameters.aspect_ratio",
        "parameters.resolution",
        "parameters.audio",
        "inputs.image",
        "parameters.negative_prompt",
    }.issubset(fields(result))
    assert result["defaults"]["quote_first"] is True


def test_tts_music_and_transcription_plans() -> None:
    tts_catalog = FakeCatalog(model("tts-1", {"voices": ["Alice", "Bob"]}))
    tts = Planner(tts_catalog).plan(  # type: ignore[arg-type]
        "audio.tts", model="tts-1", prompt="hello"
    )
    assert {"parameters.voice", "parameters.response_format", "parameters.speed"}.issubset(
        fields(tts)
    )

    music_catalog = FakeCatalog(
        model(
            "music-1",
            {
                "durations": [15, 30],
                "supports_force_instrumental": True,
                "supports_lyrics": True,
            },
        )
    )
    music = Planner(music_catalog).plan(  # type: ignore[arg-type]
        "audio.generate", model="music-1", prompt="ambient"
    )
    assert {
        "parameters.duration_seconds",
        "parameters.force_instrumental",
        "parameters.lyrics",
    }.issubset(fields(music))

    asr_catalog = FakeCatalog(model("asr-1", {}))
    asr = Planner(asr_catalog).plan(  # type: ignore[arg-type]
        "audio.transcribe", model="asr-1"
    )
    assert fields(asr) == {"inputs.audio"}


def test_missing_prompt_question_and_unsupported_operation() -> None:
    catalog = FakeCatalog(model("tts-1", {}))
    result = Planner(catalog).plan("audio.tts", model="tts-1")  # type: ignore[arg-type]
    assert "prompt" in fields(result)
    with pytest.raises(ValueError, match="Unsupported operation"):
        Planner(catalog).plan("unknown.operation")  # type: ignore[arg-type]
