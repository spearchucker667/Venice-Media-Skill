from __future__ import annotations

from pathlib import Path
from typing import Any

from venice_media_skill.catalog import ModelCatalog


class FakeClient:
    def __init__(self) -> None:
        self.calls = 0

    def get_json(self, _path: str, *, params: dict[str, Any] | None = None) -> Any:
        self.calls += 1
        return {"data": [{"id": f"{params['type']}-model"}]}


def test_catalog_caches_models(tmp_path: Path) -> None:
    client = FakeClient()
    catalog = ModelCatalog(client, tmp_path / "models.json")  # type: ignore[arg-type]
    assert catalog.list("image")[0]["id"] == "image-model"
    assert catalog.list("image")[0]["id"] == "image-model"
    assert client.calls == 1


def test_catalog_get_falls_back_to_all(tmp_path: Path) -> None:
    class FallbackClient:
        def get_json(self, _path: str, *, params: dict[str, Any] | None = None) -> Any:
            if params and params["type"] == "image":
                return {"data": []}
            return {"data": [{"id": "target"}]}

    catalog = ModelCatalog(FallbackClient(), tmp_path / "models.json")  # type: ignore[arg-type]
    assert catalog.get("target", "image") == {"id": "target"}
