"""Dynamic Venice model discovery and cache."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .client import VeniceClient
from .consent import _acquire_lock, _release_lock
from .errors import OutputError
from .output import atomic_write_text

_MODEL_TYPES = {
    "all",
    "asr",
    "embedding",
    "image",
    "music",
    "text",
    "tts",
    "upscale",
    "inpaint",
    "video",
    "code",
}


@dataclass(slots=True)
class ModelCatalog:
    client: VeniceClient
    cache_file: Path
    cache_ttl_seconds: int = 3600

    def list(self, model_type: str = "all", *, refresh: bool = False) -> list[dict[str, Any]]:
        if model_type not in _MODEL_TYPES:
            raise ValueError(f"Unsupported model type: {model_type}")
        if not refresh:
            cached = self._read_cache()
            if cached is not None:
                fpt = cached.get("fetched_per_type", {})
                fetched = fpt.get(model_type)
                if isinstance(fetched, (int, float)) and time.time() - float(fetched) <= self.cache_ttl_seconds:
                    value = cached.get("by_type", {}).get(model_type)
                    if isinstance(value, list):
                        return [item for item in value if isinstance(item, dict)]
                cached = None  # fresh fetch needed
        else:
            cached = None
        payload = self.client.get_json("/models", params={"type": model_type})
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise OutputError("GET /models returned an unexpected response shape.")
        models = [item for item in payload["data"] if isinstance(item, dict)]
        cache: dict[str, Any] = cached if isinstance(cached, dict) else {"by_type": {}, "fetched_per_type": {}}
        if not isinstance(cache.get("by_type"), dict):
            cache["by_type"] = {}
        if not isinstance(cache.get("fetched_per_type"), dict):
            cache["fetched_per_type"] = {}
        cache["fetched_per_type"][model_type] = time.time()
        cache["by_type"][model_type] = models
        self._write_cache(cache)
        return models

    def get(self, model_id: str, model_type: str = "all", *, refresh: bool = False) -> dict[str, Any] | None:
        for model in self.list(model_type, refresh=refresh):
            if model.get("id") == model_id:
                return model
        if model_type != "all":
            for model in self.list("all", refresh=refresh):
                if model.get("id") == model_id:
                    return model
        return None

    def _read_cache(self) -> dict[str, Any] | None:
        if not self.cache_file.is_file():
            return None
        try:
            payload = json.loads(self.cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        # Migrate from v1 global fetched_at to v2 per-type timestamps.
        fpt = payload.get("fetched_per_type")
        if not isinstance(fpt, dict):
            global_fetched = payload.get("fetched_at")
            if isinstance(global_fetched, (int, float)):
                fpt = {}
                for t in _MODEL_TYPES:
                    if t in payload.get("by_type", {}):
                        fpt[t] = float(global_fetched)
                payload["fetched_per_type"] = fpt
        if not isinstance(fpt, dict):
            return None
        return payload

    def _write_cache(self, payload: dict[str, Any]) -> None:
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        _acquire_lock(self.cache_file)
        try:
            current = self._read_cache() or {"by_type": {}, "fetched_per_type": {}}
            current_by_type = current.get("by_type")
            payload_by_type = payload.get("by_type")
            current_fpt = current.get("fetched_per_type", {})
            payload_fpt = payload.get("fetched_per_type", {})
            if not isinstance(current_by_type, dict):
                current_by_type = {}
            if isinstance(payload_fpt, dict):
                current_fpt.update(payload_fpt)
            # Every write here represents a *fresh* fetch, so unconditionally
            # overwrite the entries we just produced. The age gate that
            # decides whether to refetch lives in ``list()`` (read side),
            # not here. Keeping the previous ``if fpt fresh, skip`` clause
            # made the cache threshold about whether the previous copy was
            # captured — so a second fetch within TTL silently discarded
            # the new ``data`` while still bumping ``fetched_per_type``,
            # making the cache stale forever.
            if isinstance(payload_by_type, dict):
                for key, value in payload_by_type.items():
                    current_by_type[key] = value
            current["by_type"] = current_by_type
            current["fetched_per_type"] = current_fpt
            atomic_write_text(self.cache_file, json.dumps(current, indent=2, sort_keys=True) + "\n")
        finally:
            _release_lock(self.cache_file)
