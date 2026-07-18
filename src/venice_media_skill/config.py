"""Environment and filesystem configuration."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from platformdirs import user_cache_path, user_config_path, user_state_path

from .errors import ConfigurationError

DEFAULT_BASE_URL = "https://api.venice.ai/api/v1"
APP_NAME = "venice-media-skill"


@dataclass(frozen=True, slots=True)
class Settings:
    base_url: str
    api_key: str | None
    config_dir: Path
    cache_dir: Path
    state_dir: Path
    output_dir: Path
    timeout_seconds: float = 120.0

    @property
    def jobs_dir(self) -> Path:
        return self.state_dir / "jobs"

    @property
    def model_cache_file(self) -> Path:
        return self.cache_dir / "models.json"

    @classmethod
    def load(
        cls,
        *,
        require_api_key: bool = False,
        environ: dict[str, str] | None = None,
    ) -> Settings:
        env = os.environ if environ is None else environ
        config_dir = Path(env.get("VENICE_MEDIA_CONFIG_DIR", user_config_path(APP_NAME)))
        cache_dir = Path(env.get("VENICE_MEDIA_CACHE_DIR", user_cache_path(APP_NAME)))
        state_dir = Path(env.get("VENICE_MEDIA_STATE_DIR", user_state_path(APP_NAME)))
        config = _load_json_config(config_dir / "config.json")
        base_url = str(env.get("VENICE_BASE_URL", config.get("base_url", DEFAULT_BASE_URL))).rstrip("/")
        api_key = env.get("VENICE_API_KEY")
        output_dir = Path(
            env.get(
                "VENICE_MEDIA_OUTPUT_DIR",
                str(config.get("output_dir", Path.cwd() / "venice-media-output")),
            )
        ).expanduser()
        try:
            timeout_seconds = float(env.get("VENICE_MEDIA_TIMEOUT", config.get("timeout_seconds", 120)))
        except (TypeError, ValueError) as exc:
            raise ConfigurationError("VENICE_MEDIA_TIMEOUT/timeout_seconds must be a finite number.") from exc

        # Validate paths - prevent path traversal and ensure they're within reasonable bounds
        for name, path in [
            ("config_dir", config_dir),
            ("cache_dir", cache_dir),
            ("state_dir", state_dir),
            ("output_dir", output_dir),
        ]:
            _validate_safe_path(path, name)

        # Validate timeout
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ConfigurationError(f"timeout_seconds must be positive, got {timeout_seconds}")
        if timeout_seconds > 86400:  # 24 hours max
            raise ConfigurationError(f"timeout_seconds too large (max 86400), got {timeout_seconds}")

        if require_api_key and not api_key:
            raise ConfigurationError(
                "VENICE_API_KEY is not set. Export it in the host shell; the bridge never stores API keys."
            )
        return cls(
            base_url=base_url,
            api_key=api_key,
            config_dir=config_dir,
            cache_dir=cache_dir,
            state_dir=state_dir,
            output_dir=output_dir,
            timeout_seconds=timeout_seconds,
        )

    def ensure_directories(self) -> None:
        for path in (
            self.config_dir,
            self.cache_dir,
            self.state_dir,
            self.jobs_dir,
            self.output_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


def _validate_safe_path(path: Path, name: str) -> None:
    """Validate that a configured application directory path is safe to use.

    Accepts:
    - Absolute POSIX paths: /home/user/.config/venice-media-skill
    - Absolute Windows paths with drive letters: C:\\Users\\name\\AppData\\Local\\...
    - Paths not yet created on disk (resolved with strict=False)

    Rejects:
    - Null bytes in the path string
    - UNC paths (\\\\server\\share or //server/share)
    - Filesystem roots (/, C:\\, D:\\, etc.)
    - POSIX protected system directories (/etc, /usr, /bin, /sbin, /lib, /lib64)
    """
    path_str = str(path)
    if "\x00" in path_str:
        raise ConfigurationError(f"{name} contains null bytes")

    # Check for UNC paths (starts with // or \\)
    if path_str.startswith("\\\\") or path_str.startswith("//"):
        raise ConfigurationError(f"{name} must not be a UNC path: {path_str}")

    # Resolve safely without requiring the path to already exist so that CI
    # can supply valid temporary directories that have not been created yet.
    try:
        resolved = path.expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ConfigurationError(f"{name} path invalid: {exc}") from exc

    # Reject bare filesystem roots: Path("/"), Path("C:\\"), etc.
    # resolved.anchor is a str (e.g. "/" or "C:\\"); wrap in Path to compare.
    if resolved == Path(resolved.anchor):
        raise ConfigurationError(f"{name} resolves to a filesystem root: {resolved}")

    # On POSIX systems, reject writes into protected system directories.
    # Use Path equality and the .parents chain — never str.startswith() — to
    # avoid incorrectly matching unrelated paths like /usr-local or /usr_backup.
    # Resolve each protected directory too so that symlinks (e.g. macOS
    # /etc → /private/etc) are handled correctly.
    if os.name != "nt":
        protected = [
            Path("/etc"),
            Path("/usr"),
            Path("/bin"),
            Path("/sbin"),
            Path("/lib"),
            Path("/lib64"),
        ]
        for protected_dir in protected:
            canonical = protected_dir.resolve(strict=False)
            if resolved == canonical or canonical in resolved.parents:
                raise ConfigurationError(f"{name} resolves to a protected system directory: {resolved}")


def _load_json_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Unable to read config file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigurationError(f"Config file {path} must contain a JSON object.")
    forbidden = {"api_key", "token", "authorization", "venice_api_key"}.intersection(key.lower() for key in payload)
    if forbidden:
        raise ConfigurationError(f"Config file {path} contains a credential-like field. Use VENICE_API_KEY instead.")
    supported = {"base_url", "output_dir", "timeout_seconds"}
    unknown = set(payload) - supported
    if unknown:
        raise ConfigurationError(f"Unsupported config key(s): {', '.join(sorted(unknown))}")
    return payload
