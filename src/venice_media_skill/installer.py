"""Install the bundled Agent Skill into user or project discovery directories."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
import uuid
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

from .errors import ConfigurationError

SUPPORTED_HOSTS = {"generic", "kimi", "all"}
SUPPORTED_SCOPES = {"user", "project"}
_BACKUP_PREFIX = ".rollback-"
_BACKUP_METADATA_SUFFIX = ".metadata.json"


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

    destinations: list[Path] = []
    if host in {"generic", "all"}:
        destinations.append(generic_destination)
    if host in {"kimi", "all"}:
        destinations.append(kimi_destination)

    _refuse_orphan_backups(destinations)
    source = files("venice_media_skill").joinpath("assets", "skill")
    installed: list[str] = []
    for destination in destinations:
        _validate_destination_type(destination)
        installed_path = _atomic_install(source, destination)
        installed.append(installed_path)

    return {
        "status": "installed",
        "host": host,
        "scope": scope,
        "skill_paths": installed,
        "next_step": ("Start a new host-agent session. For Kimi Code, invoke /skill:venice-media <request>."),
    }


def _validate_destination_type(destination: Path) -> None:
    """Reject destinations that cannot be safely rotated in place."""
    parent = destination.parent
    if not parent.exists():
        return
    for ancestor in (parent, *parent.parents):
        if ancestor.is_symlink():
            raise ConfigurationError(f"Refusing to install under symlinked ancestor: {ancestor}")
    if destination.is_symlink():
        raise ConfigurationError(f"Refusing to replace symlinked skill destination: {destination}")
    if destination.exists() and not destination.is_dir():
        raise ConfigurationError(f"Skill destination is not a directory, refusing to clobber {destination!r}")


def _refuse_orphan_backups(destinations: list[Path]) -> None:
    """Refuse to install if a previous run left a recovery backup behind.

    The previous-crash indicator is an undo-by-undo: the backup directory's
    metadata sidecar lists the destination it was meant to protect. If we
    silently rmtree that directory on entry we would destroy forensics before
    the user can recover. They must either delete it explicitly or recover to
    that destination themselves.
    """
    orphans: list[str] = []
    for destination in destinations:
        parent = destination.parent
        if not parent.is_dir():
            continue
        for entry in parent.iterdir():
            if not entry.name.startswith(f".{destination.name}{_BACKUP_PREFIX}"):
                continue
            metadata = _load_backup_metadata(entry)
            if metadata and metadata.get("destination") == str(destination):
                orphans.append(str(entry))
            else:
                orphans.append(str(entry))
    if orphans:
        listed = "\n".join(f"  - {path}" for path in orphans)
        raise ConfigurationError(
            "Refusing to install: previous install left an unrecovered backup.\n"
            "Inspect and either remove or recover from each backup, then retry:\n"
            f"{listed}"
        )


def _atomic_install(source: Any, destination: Path) -> str:
    """Replace *destination* with a fresh copy of *source* transactionally.

    On entry: clean up an empty staging directory left behind by an interrupted
    previous install in the same destination parent. A backup directory left
    behind is owned by ``_refuse_orphan_backups`` and never touched here.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging.", dir=destination.parent))
    backup: Path | None = None
    backup_metadata: Path | None = None
    try:
        _copy_resource_tree(source, staging)
        if not (staging / "SKILL.md").is_file():
            raise ConfigurationError("Bundled skill is missing required SKILL.md")
        if destination.exists():
            backup, backup_metadata = _make_unique_backup(destination)
            os.replace(destination, backup)
        os.replace(staging, destination)
        if backup is not None:
            _safe_rmtree(backup)
            _safe_unlink(backup_metadata)
        return str(destination.resolve())
    except Exception:
        if backup is not None:
            if backup.is_dir():
                if destination.exists():
                    _safe_rmtree(destination)
                os.replace(backup, destination)
            _safe_unlink(backup_metadata)
        _safe_rmtree(staging)
        raise


def _make_unique_backup(destination: Path) -> tuple[Path, Path]:
    """Return unique ``(backup_dir, metadata_file)`` paths alongside *destination*."""
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = uuid.uuid4().hex[:8]
    parent = destination.parent
    backup = parent / f".{destination.name}{_BACKUP_PREFIX}{timestamp}-{suffix}"
    metadata = backup.with_name(backup.name + _BACKUP_METADATA_SUFFIX)
    payload = {
        "schema": "vms-backup-v1",
        "destination": str(destination),
        "created_at": datetime.now(UTC).isoformat(),
        "pid": os.getpid(),
    }
    metadata.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return backup, metadata


def _load_backup_metadata(backup: Path) -> dict[str, Any] | None:
    metadata_path = backup.with_name(backup.name + _BACKUP_METADATA_SUFFIX)
    if not metadata_path.is_file():
        return None
    try:
        loaded: Any = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _safe_rmtree(path: Path) -> None:
    if path.is_symlink():
        _safe_unlink(path)
        return
    if not path.exists():
        return
    shutil.rmtree(path)


def _safe_unlink(path: Path | None) -> None:
    if path is None:
        return
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


def _copy_resource_tree(source: Any, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        target = destination / child.name
        if child.is_dir():
            _copy_resource_tree(child, target)
        else:
            with child.open("rb") as source_handle, target.open("wb") as target_handle:
                shutil.copyfileobj(source_handle, target_handle)
