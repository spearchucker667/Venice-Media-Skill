"""Dynamic Venice model discovery and cache."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .client import VeniceClient
from .errors import OutputError

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
        cached = None if refresh else self._read_cache()
        if cached is not None and model_type in cached.get("by_type", {}):
            value = cached["by_type"][model_type]
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        payload = self.client.get_json("/models", params={"type": model_type})
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise OutputError("GET /models returned an unexpected response shape.")
        models = [item for item in payload["data"] if isinstance(item, dict)]
        cache = cached or {"fetched_at": time.time(), "by_type": {}}
        if not isinstance(cache.get("by_type"), dict):
            cache["by_type"] = {}
        cache["fetched_at"] = time.time()
        cache["by_type"][model_type] = models
        self._write_cache(cache)
        return models

    def get(
        self, model_id: str, model_type: str = "all", *, refresh: bool = False
    ) -> dict[str, Any] | None:
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
        fetched_at = payload.get("fetched_at")
        if not isinstance(fetched_at, (int, float)):
            return None
        if time.time() - float(fetched_at) > self.cache_ttl_seconds:
            return None
        return payload

    def _write_cache(self, payload: dict[str, Any]) -> None:
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.cache_file)
