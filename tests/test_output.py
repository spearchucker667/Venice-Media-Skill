from __future__ import annotations

import base64
from pathlib import Path

from venice_media_skill.client import ApiResponse
from venice_media_skill.output import ArtifactWriter

_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xfa\xff\xff?\x03\x00\x05\xfe\x02\xfe\xa3\x9a\xfa\x05"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_binary_artifact_and_sidecar(tmp_path: Path) -> None:
    writer = ArtifactWriter(tmp_path)
    artifacts = writer.save_response(
        ApiResponse(200, "image/png", {}, content=_PNG),
        operation="image.generate",
        output_dir=None,
        filename="result.png",
        overwrite=False,
        write_metadata=True,
        metadata={"model": "m"},
    )
    assert Path(artifacts[0]["path"]).read_bytes() == _PNG
    assert Path(artifacts[0]["metadata_path"]).is_file()


def test_base64_json_artifact(tmp_path: Path) -> None:
    encoded = base64.b64encode(_PNG).decode()
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
    assert Path(artifacts[0]["path"]).read_bytes() == _PNG
