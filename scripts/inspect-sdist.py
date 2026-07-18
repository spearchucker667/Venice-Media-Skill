#!/usr/bin/env python
"""Inspect the just-built sdist tarball for forbidden entries.

Run *after* ``python -m build``. Walks every member and:
- rejects archive names that contain forbidden tokens (virtualenvs,
  caches, credentials, secrets, git metadata, editor scratch);
- rejects symlink entries whose link target is absolute or escapes the
  archive root.

Any violation prints a precise ``drift:`` line on stderr and exits 1.

Usage:
    python scripts/inspect-sdist.py
"""

from __future__ import annotations

import os
import sys
import tarfile
import tomllib
from pathlib import Path

FORBIDDEN_TOKENS: tuple[str, ...] = (
    ".venv/",
    "venv/",
    ".audit-venv/",
    "__pycache__/",
    ".git/",
    "node_modules/",
    "/.env",
    ".env/",
    ".key",
    ".pem",
    ".p12",
    "id_rsa",
    ".pyc",
    ".ruff_cache/",
    ".mypy_cache/",
    ".pytest_cache/",
    ".tox/",
    ".nox/",
    "Thumbs.db",
    ".DS_Store",
)


def _project_version(root: Path) -> str:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(project["project"]["version"])


def _find_sdist(dist_dir: Path, version: str) -> Path | None:
    candidate = dist_dir / f"venice_media_skill-{version}.tar.gz"
    return candidate if candidate.is_file() else None


def _is_symlink_escape(link_target: str) -> bool:
    """True if the link target is absolute or ``..``s out of the root."""
    if link_target.startswith(("/", "\\")):
        return True
    normalized = os.path.normpath(link_target)
    return normalized.startswith("..") or os.path.isabs(normalized)


def main(dist_dir: str = "dist") -> int:
    root = Path(__file__).resolve().parent.parent
    dist_path = Path(dist_dir)
    version = _project_version(root)
    sdist = _find_sdist(dist_path, version)
    if sdist is None:
        print(
            f"inspect-sdist: expected {dist_path / f'venice_media_skill-{version}.tar.gz'}",
            file=sys.stderr,
        )
        return 1

    violations: list[str] = []
    try:
        with tarfile.open(sdist, mode="r:gz") as tf:
            for member in tf.getmembers():
                # Relative path within the archive root.
                rel = member.name.split(":", 1)[-1].lstrip("./")

                for token in FORBIDDEN_TOKENS:
                    needle = token.lstrip("/")
                    if needle and needle in rel:
                        violations.append(f"drift[name]: {sdist.name}:{member.name} matches {token!r}")
                if member.issym() or member.islnk():
                    target = member.linkname
                    if _is_symlink_escape(target):
                        violations.append(f"drift[symlink]: {sdist.name}:{member.name} -> {target!r}")
    except tarfile.TarError as exc:
        print(f"inspect-sdist: failed to read {sdist}: {exc}", file=sys.stderr)
        return 1

    if violations:
        for line in violations:
            print(line, file=sys.stderr)
        print(
            f"inspect-sdist: {len(violations)} violation(s) in {sdist.name}",
            file=sys.stderr,
        )
        return 1

    print(f"inspect-sdist: {sdist.name} clean ({sdist.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
