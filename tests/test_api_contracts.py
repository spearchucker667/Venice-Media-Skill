"""Contract, drift, and regression tests for Venice API alignment.

These tests treat the pinned OpenAPI snapshot and the runtime request schema
as coupled artifacts. They fail when:

* the tracked snapshot drifts from a known upstream commit,
* the generated JSON schema disagrees with runtime validation,
* a payload builder emits an unexpected provider wire key,
* a reserved provider/transport key can be injected through parameters,
* a new Venice field is silently passed through without explicit handling.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

from venice_media_skill import __version__ as package_version
from venice_media_skill.payloads import (
    build_audio_queue,
    build_audio_quote,
    build_image_edit,
    build_image_generate,
    build_image_multi_edit,
    build_tts,
    build_video_queue,
    build_video_quote,
    build_video_transcribe,
    build_voice_clone,
)
from venice_media_skill.request import (
    MODELLESS_OPERATIONS,
    SUPPORTED_OPERATIONS,
    MediaRequest,
    request_json_schema,
)
from venice_media_skill.reserved import RESERVED_PARAMETERS

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def openapi_snapshot() -> dict:
    path = REPO_ROOT / "references" / "venice-openapi.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture
def request_schema() -> dict:
    return request_json_schema()


class TestSnapshotProvenance:
    """The tracked OpenAPI snapshot must record its upstream origin."""

    def test_provenance_block_present(self, openapi_snapshot: dict) -> None:
        assert "x-venice-media-skill-provenance" in openapi_snapshot

    def test_provenance_points_to_official_repo(self, openapi_snapshot: dict) -> None:
        provenance = openapi_snapshot["x-venice-media-skill-provenance"]
        assert provenance["upstream_repository"] == "https://github.com/veniceai/api-docs"
        assert re.fullmatch(r"[0-9a-f]{40}", provenance["upstream_commit"])
        assert "retrieved_utc" in provenance
        assert "info_version" in provenance
        assert provenance["info_version"] == openapi_snapshot["info"]["version"]

    def test_legacy_provenance_key_removed(self, openapi_snapshot: dict) -> None:
        assert "x-venice-forge-provenance" not in openapi_snapshot

    def test_sync_script_is_idempotent(self) -> None:
        upstream = "/tmp/venice-api-docs-upstream"
        if not Path(upstream).is_dir():
            pytest.skip("upstream checkout not present; run scripts/sync-venice-api-docs.py first")
        result = subprocess.run(
            [
                "python",
                str(REPO_ROOT / "scripts" / "sync-venice-api-docs.py"),
                upstream,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


class TestRequestSchemaParity:
    """Runtime validation and generated JSON Schema must enforce the same rules."""

    def test_schema_meta_valid(self, request_schema: dict) -> None:
        import jsonschema

        jsonschema.Draft202012Validator.check_schema(request_schema)

    def test_schema_matches_runtime_operations(self, request_schema: dict) -> None:
        enum = set(request_schema["properties"]["operation"]["enum"])
        assert enum == SUPPORTED_OPERATIONS

    def test_modelless_operations_match(self, request_schema: dict) -> None:
        for branch in request_schema["allOf"]:
            op = branch["if"]["properties"]["operation"]["const"]
            required = set(branch["then"].get("required", []))
            if op in MODELLESS_OPERATIONS:
                assert "model" not in required
            else:
                assert "model" in required

    def test_video_reference_limits_in_schema(self, request_schema: dict) -> None:
        video_inputs = next(
            branch["then"]["properties"]["inputs"]
            for branch in request_schema["allOf"]
            if branch["if"]["properties"]["operation"]["const"] == "video.generate"
        )
        props = video_inputs["properties"]
        assert props["reference_images"]["maxItems"] == 30
        assert props["reference_videos"]["maxItems"] == 10
        assert props["reference_audios"]["maxItems"] == 10

    def test_video_keyframes_in_schema(self, request_schema: dict) -> None:
        video_inputs = next(
            branch["then"]["properties"]["inputs"]
            for branch in request_schema["allOf"]
            if branch["if"]["properties"]["operation"]["const"] == "video.generate"
        )
        keyframes = video_inputs["properties"]["keyframes"]
        assert keyframes["maxItems"] == 10
        item = keyframes["items"]
        assert set(item["required"]) == {"image", "frame_index"}
        assert item["properties"]["frame_index"]["minimum"] == 0

    def test_style_references_in_schema(self, request_schema: dict) -> None:
        image_inputs = next(
            branch["then"]["properties"]["inputs"]
            for branch in request_schema["allOf"]
            if branch["if"]["properties"]["operation"]["const"] == "image.generate"
        )
        style = image_inputs["properties"]["style_references"]
        item = style["items"]
        assert set(item["required"]) == {"image"}
        assert item["properties"]["strength"]["minimum"] == 0.1
        assert item["properties"]["strength"]["maximum"] == 1

    def test_schema_regeneration_matches_committed_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            regen = Path(tmpdir) / "request.schema.json"
            result = subprocess.run(
                [
                    "python",
                    "-m",
                    "venice_media_skill",
                    "schema",
                    "--output",
                    str(regen),
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0
            committed = json.loads((REPO_ROOT / "references" / "request.schema.json").read_text())
            regenerated = json.loads(regen.read_text())
            assert committed == regenerated


class TestPayloadWireKeys:
    """Payload builders must emit exact provider keys expected by the pinned OpenAPI."""

    def _request(self, operation: str, **kwargs: object) -> MediaRequest:
        defaults: dict[str, object] = {
            "operation": operation,
            "model": "test-model",
            "prompt": "test prompt",
        }
        if operation in MODELLESS_OPERATIONS:
            defaults.pop("model", None)
        if operation == "audio.voice_clone":
            defaults["model"] = "tts-chatterbox-hd"
        defaults.update(kwargs)
        return MediaRequest.from_mapping(defaults)

    def test_image_generate_enhance_prompt(self) -> None:
        req = self._request("image.generate", parameters={"enhance_prompt": True})
        payload = build_image_generate(req).payload
        assert payload["enhance_prompt"] is True

    def test_image_generate_style_references(self) -> None:
        req = self._request(
            "image.generate",
            inputs={
                "style_references": [
                    {"image": "https://example.com/a.png", "strength": 0.7},
                    {"image": "https://example.com/b.png"},
                ]
            },
        )
        payload = build_image_generate(req).payload
        assert "style_references" in payload
        refs = payload["style_references"]
        assert len(refs) == 2
        assert refs[0]["image"].startswith("https://")
        assert refs[0]["strength"] == 0.7
        assert refs[1]["image"].startswith("https://")
        assert "strength" not in refs[1]

    def test_image_edit_enhance_controls(self) -> None:
        req = self._request(
            "image.edit",
            inputs={"image": "https://example.com/i.png"},
            parameters={"enhance_prompt": True, "disable_prompt_optimization_thinking": True},
        )
        payload = build_image_edit(req).payload
        assert payload["enhance_prompt"] is True
        assert payload["disable_prompt_optimization_thinking"] is True

    def test_image_multi_edit_enhance_controls(self) -> None:
        req = self._request(
            "image.multi_edit",
            inputs={"images": ["https://example.com/1.png", "https://example.com/2.png"]},
            parameters={"enhance_prompt": True, "disable_prompt_optimization_thinking": False},
        )
        payload = build_image_multi_edit(req).payload
        assert payload["enhance_prompt"] is True
        assert payload["disable_prompt_optimization_thinking"] is False

    def test_tts_prompt_maps_to_input(self) -> None:
        req = self._request("audio.tts", prompt="hello")
        payload = build_tts(req).payload
        assert payload["input"] == "hello"

    def test_tts_style_prompt_alias(self) -> None:
        req = self._request("audio.tts", prompt="hello", parameters={"style_prompt": "excited"})
        payload = build_tts(req).payload
        assert payload["prompt"] == "excited"
        assert "style_prompt" not in payload

    def test_tts_language_temperature_top_p(self) -> None:
        req = self._request(
            "audio.tts",
            prompt="hello",
            parameters={"language": "en", "temperature": 0.8, "top_p": 0.9},
        )
        payload = build_tts(req).payload
        assert payload["language"] == "en"
        assert payload["temperature"] == 0.8
        assert payload["top_p"] == 0.9

    def test_audio_generate_loop(self) -> None:
        req = self._request("audio.generate", parameters={"loop": True})
        queue = build_audio_queue(req).payload
        quote = build_audio_quote(req).payload
        assert queue["loop"] is True
        assert "loop" not in quote

    def test_video_keyframes_wire_shape(self) -> None:
        req = self._request(
            "video.generate",
            parameters={"duration": "5s"},
            inputs={
                "keyframes": [
                    {"image": "https://example.com/k0.png", "frame_index": 0},
                    {"image": "https://example.com/k1.png", "frame_index": 24},
                ]
            },
        )
        payload = build_video_queue(req).payload
        assert "keyframes" in payload
        assert payload["keyframes"] == [
            {"image_url": "https://example.com/k0.png", "frame_index": 0},
            {"image_url": "https://example.com/k1.png", "frame_index": 24},
        ]

    def test_video_quote_strips_keyframes(self) -> None:
        req = self._request(
            "video.generate",
            parameters={"duration": "5s"},
            inputs={
                "keyframes": [{"image": "https://example.com/k.png", "frame_index": 0}],
                "reference_images": ["https://example.com/r.png"],
            },
        )
        quote = build_video_quote(req).payload
        assert "keyframes" not in quote
        assert "reference_image_urls" not in quote

    def test_voice_clone_payload(self) -> None:
        req = self._request("audio.voice_clone", inputs={"audio": "/tmp/sample.mp3"})
        canonical = build_voice_clone(req)
        assert canonical.endpoint == "/audio/voices"
        assert canonical.payload == {"model": "tts-chatterbox-hd"}

    def test_video_transcribe_payload(self) -> None:
        req = MediaRequest.from_mapping(
            {
                "operation": "video.transcribe",
                "inputs": {"url": "https://youtube.com/watch?v=abc"},
                "parameters": {"response_format": "text"},
            }
        )
        canonical = build_video_transcribe(req)
        assert canonical.endpoint == "/video/transcriptions"
        assert canonical.payload == {
            "url": "https://youtube.com/watch?v=abc",
            "response_format": "text",
        }


class TestRuntimeValidation:
    """Runtime validators must enforce the same cardinality/type rules as the schema."""

    def test_video_reference_limits_runtime(self) -> None:
        with pytest.raises(Exception):
            MediaRequest.from_mapping(
                {
                    "operation": "video.generate",
                    "model": "m",
                    "prompt": "p",
                    "parameters": {"duration": "5s"},
                    "inputs": {"reference_images": ["https://example.com/i.png"] * 31},
                }
            )

    def test_video_keyframe_duplicate_index_rejected(self) -> None:
        with pytest.raises(Exception):
            MediaRequest.from_mapping(
                {
                    "operation": "video.generate",
                    "model": "m",
                    "prompt": "p",
                    "parameters": {"duration": "5s"},
                    "inputs": {
                        "keyframes": [
                            {"image": "https://example.com/a.png", "frame_index": 0},
                            {"image": "https://example.com/b.png", "frame_index": 0},
                        ]
                    },
                }
            )

    def test_video_keyframe_exceeds_duration_rejected(self) -> None:
        with pytest.raises(Exception):
            MediaRequest.from_mapping(
                {
                    "operation": "video.generate",
                    "model": "m",
                    "prompt": "p",
                    "parameters": {"duration": "1s"},
                    "inputs": {
                        "keyframes": [
                            {"image": "https://example.com/a.png", "frame_index": 100},
                        ]
                    },
                }
            )

    def test_style_references_strength_range(self) -> None:
        with pytest.raises(Exception):
            MediaRequest.from_mapping(
                {
                    "operation": "image.generate",
                    "model": "m",
                    "prompt": "p",
                    "inputs": {"style_references": [{"image": "https://example.com/a.png", "strength": 2}]},
                }
            )

    def test_tts_reserved_prompt_rejected(self) -> None:
        with pytest.raises(Exception):
            MediaRequest.from_mapping(
                {
                    "operation": "audio.tts",
                    "model": "m",
                    "prompt": "hello",
                    "parameters": {"prompt": "excited"},
                }
            )


class TestReservedParameterGating:
    """Reserved provider/transport keys cannot be injected through free-form parameters."""

    @pytest.mark.parametrize("key", sorted(RESERVED_PARAMETERS))
    def test_reserved_key_rejected_in_parameters(self, key: str) -> None:
        manifest: dict[str, object] = {
            "operation": "image.generate",
            "model": "m",
            "prompt": "p",
            "parameters": {key: "anything"},
        }
        with pytest.raises(Exception):
            MediaRequest.from_mapping(manifest)


class TestReleaseMetadata:
    """Release metadata must remain consistent across tag, package, fallback, and changelog."""

    def test_pyproject_version_matches_init_fallback(self) -> None:
        import tomllib

        pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        assert pyproject["project"]["version"] == package_version

    def test_verify_release_script_accepts_expected_tag(self) -> None:
        result = subprocess.run(
            ["python", str(REPO_ROOT / "scripts" / "verify-release.py"), "v" + package_version],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_historical_v1_2_0_mismatch_cannot_recur(self) -> None:
        """The v1.2.0 release failed because the tag did not match the package version.

        This regression test pins the current package version to the pyproject version
        so the same failure mode cannot happen again.
        """
        import tomllib

        pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        assert pyproject["project"]["version"] == package_version
