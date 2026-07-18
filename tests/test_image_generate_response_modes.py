from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from venice_media_skill.client import VeniceClient
from venice_media_skill.errors import ApiError, ContentValidationError, PayloadValidationError, ReservedParameterError
from venice_media_skill.jobs import JobStore
from venice_media_skill.output import ArtifactWriter
from venice_media_skill.payloads import ImageGenerationOutputPlan, build_image_generate
from venice_media_skill.request import MediaRequest, request_json_schema
from venice_media_skill.runner import MediaRunner

PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xfa\xff\xff?\x03\x00\x05\xfe\x02\xfe\xa3\x9a\xfa\x05"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\xff\xd9"
PROMPT = "A photorealistic studio portrait of a 50-year-old adult woman."


def _request(*, variants: Any = ...) -> MediaRequest:
    parameters = {} if variants is ... else {"variants": variants}
    return MediaRequest(
        operation="image.generate",
        model="lustify-v7",
        prompt=PROMPT,
        parameters=parameters,
    )


def _validated_request(*, variants: Any = ..., output: dict[str, Any] | None = None) -> MediaRequest:
    mapping: dict[str, Any] = {
        "operation": "image.generate",
        "model": "lustify-v7",
        "prompt": PROMPT,
    }
    if variants is not ...:
        mapping["parameters"] = {"variants": variants}
    if output is not None:
        mapping["output"] = output
    return MediaRequest.from_mapping(mapping)


@pytest.mark.parametrize("variants", [..., 1])
def test_single_image_binary_payload_omits_variants(variants: Any) -> None:
    canonical = build_image_generate(_request(variants=variants))
    payload = canonical.payload
    assert payload["return_binary"] is True
    assert "variants" not in payload
    assert canonical.image_output_plan == ImageGenerationOutputPlan(image_count=1, return_binary=True)
    serialized = json.dumps(payload, sort_keys=True)
    assert '"variants"' not in serialized
    assert json.loads(serialized)["return_binary"] is True


@pytest.mark.parametrize("count", [2, 3, 4])
def test_multiple_images_use_json_mode_and_preserve_count(count: int) -> None:
    canonical = build_image_generate(_request(variants=count))
    assert canonical.payload["return_binary"] is False
    assert canonical.payload["variants"] == count
    assert json.loads(json.dumps(canonical.payload))["variants"] == count
    assert canonical.image_output_plan == ImageGenerationOutputPlan(image_count=count, return_binary=False)


@pytest.mark.parametrize("invalid", [0, -1, 5, 1.5, "2", True, None])
def test_invalid_image_counts_fail_in_payload_builder(invalid: Any) -> None:
    with pytest.raises(PayloadValidationError, match=r"integer in \[1, 4\]"):
        build_image_generate(_request(variants=invalid))


@pytest.mark.parametrize("invalid", [0, -1, 5, 1.5, "2", True, None])
def test_invalid_image_counts_fail_in_manifest_validation(invalid: Any) -> None:
    with pytest.raises(PayloadValidationError, match="variants"):
        _validated_request(variants=invalid)


def test_return_binary_remains_bridge_controlled() -> None:
    with pytest.raises(ReservedParameterError):
        MediaRequest.from_mapping(
            {
                "operation": "image.generate",
                "model": "lustify-v7",
                "prompt": PROMPT,
                "parameters": {"return_binary": True, "variants": 1},
            }
        )


def test_prompt_and_model_are_preserved_exactly() -> None:
    payload = build_image_generate(_validated_request()).payload
    assert payload["model"] == "lustify-v7"
    assert payload["prompt"] == PROMPT
    assert "50-year-old adult" in payload["prompt"]


def test_manifest_schema_documents_canonical_variants_contract() -> None:
    schema = request_json_schema()
    variants = schema["$defs"]["parameterShapes"]["image.generate"]["properties"]["variants"]
    assert variants["type"] == "integer"
    assert variants["minimum"] == 1
    assert variants["maximum"] == 4
    assert "binary response mode" in variants["description"]


def test_dry_run_exposes_single_binary_output_plan(tmp_path: Path) -> None:
    request = _validated_request()
    request.execution.dry_run = True
    runner = MediaRunner(
        client=None,  # type: ignore[arg-type]
        writer=ArtifactWriter(tmp_path / "output"),
        jobs=JobStore(tmp_path / "jobs"),
    )
    result = runner.run(request)
    assert result["output_plan"] == {
        "image_count": 1,
        "response_mode": "binary",
        "variants_field": "omitted",
    }
    assert "variants" not in result["api_request"]


def test_dry_run_exposes_multiple_json_output_plan(tmp_path: Path) -> None:
    request = _validated_request(variants=4)
    request.execution.dry_run = True
    runner = MediaRunner(
        client=None,  # type: ignore[arg-type]
        writer=ArtifactWriter(tmp_path / "output"),
        jobs=JobStore(tmp_path / "jobs"),
    )
    result = runner.run(request)
    assert result["output_plan"] == {
        "image_count": 4,
        "response_mode": "json",
        "variants_field": "included",
        "variants": 4,
    }


def test_mock_transport_rejects_old_combination_and_accepts_corrected_binary_request(tmp_path: Path) -> None:
    observed: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        observed.append(body)
        if body.get("return_binary") is True and "variants" in body:
            return httpx.Response(
                400,
                json={"error": {"message": "variants is only supported when return_binary is false"}},
            )
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    with VeniceClient(
        base_url="https://api.example.test/api/v1",
        api_key="test_venice_key_not_real",
        transport=httpx.MockTransport(handler),
        allow_noncanonical_endpoint=True,
    ) as client:
        with pytest.raises(ApiError) as old_error:
            client.request(
                "POST",
                "/image/generate",
                json_body={"model": "lustify-v7", "prompt": PROMPT, "return_binary": True, "variants": 1},
            )
        assert old_error.value.status_code == 400
        runner = MediaRunner(
            client=client,
            writer=ArtifactWriter(tmp_path / "output"),
            jobs=JobStore(tmp_path / "jobs"),
        )
        result = runner.run(_validated_request(output={"filename": "portrait.png"}))

    corrected = observed[1]
    assert corrected["return_binary"] is True
    assert "variants" not in corrected
    artifact = result["artifacts"][0]
    saved = Path(artifact["path"])
    assert saved.read_bytes() == PNG
    assert saved.suffix == ".png"
    assert artifact["bytes"] == len(PNG)
    assert len(artifact["sha256"]) == 64
    metadata = json.loads(Path(artifact["metadata_path"]).read_text(encoding="utf-8"))
    assert metadata["artifact"]["sha256"] == artifact["sha256"]


def test_mock_transport_writes_ordered_multi_image_json_artifacts(tmp_path: Path) -> None:
    encoded = [base64.b64encode(PNG).decode("ascii"), base64.b64encode(JPEG).decode("ascii")]
    observed: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "generate-image-test",
                "images": encoded,
                "timing": {
                    "inferenceDuration": 1,
                    "inferencePreprocessingTime": 1,
                    "inferenceQueueTime": 1,
                    "total": 3,
                },
            },
        )

    with VeniceClient(
        base_url="https://api.example.test/api/v1",
        api_key="test_venice_key_not_real",
        transport=httpx.MockTransport(handler),
        allow_noncanonical_endpoint=True,
    ) as client:
        runner = MediaRunner(
            client=client,
            writer=ArtifactWriter(tmp_path / "output"),
            jobs=JobStore(tmp_path / "jobs"),
        )
        result = runner.run(_validated_request(variants=2, output={"filename": "portrait.png", "write_metadata": True}))

    assert observed["return_binary"] is False
    assert observed["variants"] == 2
    artifacts = result["artifacts"]
    assert [Path(item["path"]).name for item in artifacts] == ["portrait-1.png", "portrait-2.jpg"]
    assert [Path(item["path"]).read_bytes() for item in artifacts] == [PNG, JPEG]
    assert [item["variant_index"] for item in artifacts] == [1, 2]
    assert all(item["variant_count"] == 2 for item in artifacts)
    for index, artifact in enumerate(artifacts, start=1):
        metadata = json.loads(Path(artifact["metadata_path"]).read_text(encoding="utf-8"))
        assert metadata["artifact"]["variant_index"] == index
        assert metadata["artifact"]["variant_count"] == 2


def test_malformed_multi_image_response_is_atomic(tmp_path: Path) -> None:
    output = tmp_path / "output"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"images": [base64.b64encode(PNG).decode("ascii"), "%%%malformed-base64%%%"]},
        )

    with VeniceClient(
        base_url="https://api.example.test/api/v1",
        api_key="test_venice_key_not_real",
        transport=httpx.MockTransport(handler),
        allow_noncanonical_endpoint=True,
    ) as client:
        runner = MediaRunner(
            client=client,
            writer=ArtifactWriter(output),
            jobs=JobStore(tmp_path / "jobs"),
        )
        with pytest.raises(ContentValidationError, match="invalid base64"):
            runner.run(_validated_request(variants=2, output={"filename": "portrait.png"}))

    assert not list(output.glob("portrait*"))
