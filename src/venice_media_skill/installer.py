"""Install the bundled Agent Skill into user or project discovery directories."""

from __future__ import annotations

import os
import shutil
from importlib.resources import files
from pathlib import Path
from typing import Any

from .errors import ConfigurationError

SUPPORTED_HOSTS = {"generic", "kimi", "all"}
SUPPORTED_SCOPES = {"user", "project"}


def install_skill(
    *,
    host: str,
    scope: str,
    project_dir: str | None = None,
) -> dict[str, Any]:
    if host not in SUPPORTED_HOSTS:
        raise ConfigurationError(f"Unsupported host: {host}")
    if scope not in SUPPORTED_SCOPES:
        raise ConfigurationError(f"Unsupported scope: {scope}")

    if scope == "project":
        root = Path(project_dir or Path.cwd()).expanduser().resolve()
        if not root.is_dir():
            raise ConfigurationError(f"Project directory does not exist: {root}")
        generic_destination = root / ".agents" / "skills" / "venice-media"
        kimi_destination = root / ".kimi-code" / "skills" / "venice-media"
    else:
        home = Path.home()
        generic_destination = home / ".agents" / "skills" / "venice-media"
        kimi_home = Path(os.environ.get("KIMI_CODE_HOME", home / ".kimi-code")).expanduser()
        kimi_destination = kimi_home / "skills" / "venice-media"

    # Host selector is exclusive: ``--host kimi`` installs only the Kimi
    # discovery root; ``--host generic`` only the generic ``.agents`` root.
    # ``--host all`` installs both. This avoids silently placing the
    # generic skill in environments whose host is Kimi (where the discovery
    # root is ``~/.kimi-code``) and vice versa.
    destinations: list[Path] = []
    if host in {"generic", "all"}:
        destinations.append(generic_destination)
    if host in {"kimi", "all"}:
        destinations.append(kimi_destination)

    source = files("venice_media_skill").joinpath("assets", "skill")
    installed: list[str] = []
    for destination in destinations:
        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _copy_resource_tree(source, destination)
        installed.append(str(destination.resolve()))

    return {
        "status": "installed",
        "host": host,
        "scope": scope,
        "skill_paths": installed,
        "next_step": ("Start a new host-agent session. For Kimi Code, invoke /skill:venice-media <request>."),
    }


def _copy_resource_tree(source: Any, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        target = destination / child.name
        if child.is_dir():
            _copy_resource_tree(child, target)
        else:
            with child.open("rb") as source_handle, target.open("wb") as target_handle:
                shutil.copyfileobj(source_handle, target_handle)
