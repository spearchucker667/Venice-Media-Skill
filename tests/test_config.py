from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from venice_media_skill.config import Settings, _validate_safe_path
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


# ---------------------------------------------------------------------------
# Regression: Windows drive-qualified application paths
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific path semantics")
def test_settings_accepts_windows_drive_qualified_directories(tmp_path: Path) -> None:
    """Settings.load() must accept absolute Windows paths with drive letters.

    This is a regression test for the CI failure:
      ConfigurationError: config_dir must not contain drive letters: D:\\a\\_temp\\...

    On Windows, tmp_path already starts with a drive letter (e.g. C:\\Users\\...
    or D:\\a\\...), so this test directly exercises the real failure mode from
    the cross-platform-smoke GitHub Actions job.
    """
    env = {
        "VENICE_MEDIA_CONFIG_DIR": str(tmp_path / "config"),
        "VENICE_MEDIA_CACHE_DIR": str(tmp_path / "cache"),
        "VENICE_MEDIA_STATE_DIR": str(tmp_path / "state"),
        "VENICE_MEDIA_OUTPUT_DIR": str(tmp_path / "output"),
    }
    # Must not raise ConfigurationError
    settings = Settings.load(environ=env)
    settings.ensure_directories()
    assert settings.config_dir.is_dir()
    assert settings.cache_dir.is_dir()
    assert settings.state_dir.is_dir()
    assert settings.output_dir.is_dir()


# ---------------------------------------------------------------------------
# Regression: _validate_safe_path helper — UNC rejection
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name != "nt", reason="UNC path syntax is Windows-specific")
def test_validate_safe_path_rejects_unc_path_windows() -> None:
    """UNC paths must remain rejected on Windows."""
    with pytest.raises(ConfigurationError, match="UNC"):
        _validate_safe_path(Path("\\\\server\\share\\venice-media"), "config_dir")


def test_validate_safe_path_rejects_double_slash_unc() -> None:
    """//server/share style UNC paths must be rejected on all platforms."""
    with pytest.raises(ConfigurationError, match="UNC"):
        _validate_safe_path(Path("//server/share/venice-media"), "config_dir")


# ---------------------------------------------------------------------------
# Regression: filesystem-root rejection
# ---------------------------------------------------------------------------


def test_validate_safe_path_rejects_posix_root() -> None:
    """The POSIX root / must be rejected."""
    with pytest.raises(ConfigurationError, match="filesystem root"):
        _validate_safe_path(Path("/"), "config_dir")


@pytest.mark.skipif(os.name != "nt", reason="Windows drive roots are platform-specific")
def test_validate_safe_path_rejects_windows_drive_root() -> None:
    """A bare Windows drive root (C:\\) must be rejected."""
    with pytest.raises(ConfigurationError, match="filesystem root"):
        _validate_safe_path(Path("C:\\"), "config_dir")


# ---------------------------------------------------------------------------
# Regression: POSIX protected-directory rejection
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="Protected POSIX directories do not apply on Windows")
def test_validate_safe_path_rejects_etc() -> None:
    with pytest.raises(ConfigurationError, match="protected system directory"):
        _validate_safe_path(Path("/etc"), "config_dir")


@pytest.mark.skipif(os.name == "nt", reason="Protected POSIX directories do not apply on Windows")
def test_validate_safe_path_rejects_etc_subdirectory() -> None:
    with pytest.raises(ConfigurationError, match="protected system directory"):
        _validate_safe_path(Path("/etc/venice-media"), "config_dir")


@pytest.mark.skipif(os.name == "nt", reason="Protected POSIX directories do not apply on Windows")
def test_validate_safe_path_rejects_usr_subdirectory() -> None:
    with pytest.raises(ConfigurationError, match="protected system directory"):
        _validate_safe_path(Path("/usr/local/venice-media"), "config_dir")


@pytest.mark.skipif(os.name == "nt", reason="Protected POSIX directories do not apply on Windows")
def test_validate_safe_path_accepts_usr_adjacent_paths() -> None:
    """Paths like /usr-local or /usr_backup must NOT be incorrectly rejected.

    This guards against the old unsafe str.startswith("/usr") approach.
    These paths are unusual but must not be confused with /usr itself.
    """
    # These should not raise (they're odd but not protected)
    _validate_safe_path(Path("/usr-local/venice-media"), "config_dir")
    _validate_safe_path(Path("/usr_backup/venice-media"), "config_dir")


# ---------------------------------------------------------------------------
# Regression: null-byte rejection (all platforms)
# ---------------------------------------------------------------------------


def test_validate_safe_path_rejects_null_bytes() -> None:
    with pytest.raises(ConfigurationError, match="null bytes"):
        _validate_safe_path(Path("/tmp/venice\x00media"), "config_dir")


# ---------------------------------------------------------------------------
# Regression: nonexistent directories are accepted without error
# ---------------------------------------------------------------------------


def test_validate_safe_path_accepts_nonexistent_subdirectory(tmp_path: Path) -> None:
    """Paths that do not yet exist must be accepted (resolve with strict=False)."""
    nonexistent = tmp_path / "does" / "not" / "exist" / "yet"
    assert not nonexistent.exists()
    # Must not raise
    _validate_safe_path(nonexistent, "config_dir")
