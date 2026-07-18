from __future__ import annotations

import json
import threading
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


# ----- P1-02 model cache refresh -----


class _ExchangeableClient:
    """Fake client that returns ``data`` keyed by ``type`` and records every call."""

    def __init__(self, payload_by_type: dict[str, list[str]]) -> None:
        self.payload_by_type = payload_by_type
        self.calls: list[str] = []

    def get_json(self, _path: str, *, params: dict[str, Any] | None = None) -> Any:
        assert params is not None
        model_type = params["type"]
        self.calls.append(model_type)
        return {"data": [{"id": model_id} for model_id in self.payload_by_type[model_type]]}


def test_refresh_replaces_stale_model_type(tmp_path: Path) -> None:
    """A second network fetch for the same ``model_type`` MUST replace the
    stale ``data`` entry in the cache. The previous implementation gated
    the write with a "is the existing entry still fresh?" check and
    dropped the new payload while keeping the fetch timestamp, so the
    cache went permanently stale the moment ``refresh=True`` was used.
    """
    client = _ExchangeableClient({"image": ["image-old"]})
    catalog = ModelCatalog(client, tmp_path / "models.json", cache_ttl_seconds=1)  # type: ignore[arg-type]
    assert catalog.list("image")[0]["id"] == "image-old"

    # Force the existing entry to be stale past the TTL and pretend the
    # upstream returned updated data.
    raw = json.loads((tmp_path / "models.json").read_text(encoding="utf-8"))
    raw["fetched_per_type"]["image"] = 0.0  # epoch -> definitely stale
    (tmp_path / "models.json").write_text(json.dumps(raw), encoding="utf-8")

    client.payload_by_type["image"] = ["image-new"]
    assert catalog.list("image")[0]["id"] == "image-new"

    # The cache file itself should also reflect the new value so the
    # next read returns it without refetching.
    raw = json.loads((tmp_path / "models.json").read_text(encoding="utf-8"))
    assert [item["id"] for item in raw["by_type"]["image"]] == ["image-new"]


def test_concurrent_model_cache_updates_preserve_each_type(tmp_path: Path) -> None:
    """Two concurrent fetches for **different** model types must not
    trample each other's cache entries. The pre-fix _write_cache could
    drop or stall an unrelated type if the timestamp check kicked in
    while another thread was mid-write; this test exercises the happy
    case where both updates land.
    """
    import time as _time

    barrier = threading.Barrier(2)

    class _ParallelClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get_json(self, _path: str, *, params: dict[str, Any] | None = None) -> Any:
            assert params is not None
            model_type = params["type"]
            self.calls.append(model_type)
            # Both threads reach _write_cache roughly together so the
            # lock must serialize them rather than let one drop the
            # other's payload.
            barrier.wait(timeout=5.0)
            return {"data": [{"id": f"{model_type}-only"}]}

    client = _ParallelClient()
    catalog = ModelCatalog(client, tmp_path / "models.json", cache_ttl_seconds=1)  # type: ignore[arg-type]

    results: dict[str, str] = {}

    def _fetch(model_type: str) -> None:
        items = catalog.list(model_type)
        results[model_type] = items[0]["id"]
        # Force the entry to be stale immediately so a follow-up
        # in-thread read would still find it fresh-enough.
        _time.sleep(0)

    t_image = threading.Thread(target=_fetch, args=("image",))
    t_video = threading.Thread(target=_fetch, args=("video",))
    t_image.start()
    t_video.start()
    t_image.join()
    t_video.join()

    assert results["image"] == "image-only"
    assert results["video"] == "video-only"

    raw = json.loads((tmp_path / "models.json").read_text(encoding="utf-8"))
    assert [item["id"] for item in raw["by_type"]["image"]] == ["image-only"]
    assert [item["id"] for item in raw["by_type"]["video"]] == ["video-only"]
