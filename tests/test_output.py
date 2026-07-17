from __future__ import annotations

import base64
from pathlib import Path

from venice_media_skill.client import ApiResponse
from venice_media_skill.output import ArtifactWriter


def test_binary_artifact_and_sidecar(tmp_path: Path) -> None:
    writer = ArtifactWriter(tmp_path)
    artifacts = writer.save_response(
        ApiResponse(200, "image/png", {}, content=b"image"),
        operation="image.generate",
        output_dir=None,
        filename="result.png",
        overwrite=False,
        write_metadata=True,
        metadata={"model": "m"},
    )
    assert Path(artifacts[0]["path"]).read_bytes() == b"image"
    assert Path(artifacts[0]["metadata_path"]).is_file()


def test_base64_json_artifact(tmp_path: Path) -> None:
    encoded = base64.b64encode(b"image-two").decode()
    writer = ArtifactWriter(tmp_path)
    artifacts = writer.save_response(
        ApiResponse(200, "application/json", {}, json_data={"data": [{"b64_json": encoded}]}),
        operation="image.generate",
        output_dir=None,
        filename=None,
        overwrite=False,
        write_metadata=False,
        metadata={},
    )
    assert Path(artifacts[0]["path"]).read_bytes() == b"image-two"
