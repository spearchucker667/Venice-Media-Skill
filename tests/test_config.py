from __future__ import annotations

import json
from pathlib import Path

import pytest

from venice_media_skill.config import Settings
from venice_media_skill.errors import ConfigurationError


def test_settings_requires_api_key_when_requested(tmp_path: Path) -> None:
    env = {
        "VENICE_MEDIA_CONFIG_DIR": str(tmp_path / "config"),
        "VENICE_MEDIA_CACHE_DIR": str(tmp_path / "cache"),
        "VENICE_MEDIA_STATE_DIR": str(tmp_path / "state"),
        "VENICE_MEDIA_OUTPUT_DIR": str(tmp_path / "output"),
    }
    with pytest.raises(ConfigurationError, match="VENICE_API_KEY"):
        Settings.load(require_api_key=True, environ=env)


def test_settings_loads_environment_and_creates_directories(tmp_path: Path) -> None:
    env = {
        "VENICE_API_KEY": "test-key",
        "VENICE_BASE_URL": "https://example.test/api/v1/",
        "VENICE_MEDIA_CONFIG_DIR": str(tmp_path / "config"),
        "VENICE_MEDIA_CACHE_DIR": str(tmp_path / "cache"),
        "VENICE_MEDIA_STATE_DIR": str(tmp_path / "state"),
        "VENICE_MEDIA_OUTPUT_DIR": str(tmp_path / "output"),
        "VENICE_MEDIA_TIMEOUT": "42",
    }
    settings = Settings.load(require_api_key=True, environ=env)
    settings.ensure_directories()
    assert settings.base_url == "https://example.test/api/v1"
    assert settings.api_key == "test-key"
    assert settings.timeout_seconds == 42
    assert settings.jobs_dir.is_dir()
    assert settings.output_dir.is_dir()


def test_config_rejects_credential_fields(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(json.dumps({"api_key": "forbidden"}))
    env = {
        "VENICE_MEDIA_CONFIG_DIR": str(config_dir),
        "VENICE_MEDIA_CACHE_DIR": str(tmp_path / "cache"),
        "VENICE_MEDIA_STATE_DIR": str(tmp_path / "state"),
    }
    with pytest.raises(ConfigurationError, match="credential-like"):
        Settings.load(environ=env)
