#!/usr/bin/env python3
"""Fail-closed sync check for vendored skill/reference mirrors.

The ``venice-media`` bundle ships through three independent mirrors
(``skills/venice-media/``, ``adapters/kimi-code/venice-media/``, and
``src/venice_media_skill/assets/skill/``) plus the canonical source at
``references/``. Edit one location and the others will silently drift;
this script compares the bytes and exits non-zero on any mismatch so
``scripts/validate.sh`` has deterministic discovery.

Run from the repo root.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


REFERENCE_DIR_NAME = "references"


def _tree_manifest(root: Path) -> dict[str, tuple[int, str]]:
    return {
        path.relative_to(root).as_posix(): (path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _hash_targets(canonical_root: Path, relpaths: tuple[str, ...]) -> tuple[dict[str, str], list[str]]:
    """Return (relpath -> sha256) for files under ``canonical_root``."""
    canonical_hashes: dict[str, str] = {}
    mismatches: list[str] = []
    for rel in relpaths:
        target = canonical_root / rel
        if not target.exists():
            mismatches.append(f"missing canonical: {target}")
            continue
        canonical_hashes[rel] = hashlib.sha256(target.read_bytes()).hexdigest()
    return canonical_hashes, mismatches


def _check_reference_subtree(canonical_dir: Path, mirrors: tuple[Path, ...], relpaths: tuple[str, ...]) -> list[str]:
    mismatches: list[str] = []
    canonical_root = REPO_ROOT / canonical_dir
    canonical_hashes, canonical_errors = _hash_targets(canonical_root, relpaths)
    mismatches.extend(canonical_errors)
    for mirror in mirrors:
        mirror_root = REPO_ROOT / mirror / REFERENCE_DIR_NAME
        for rel, canonical_sha in canonical_hashes.items():
            target = mirror_root / rel
            if not target.exists():
                mismatches.append(f"missing mirror: {target}")
                continue
            actual_sha = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual_sha != canonical_sha:
                mismatches.append(
                    f"drift[references]: {target} (expected sha256={canonical_sha[:12]}…, got {actual_sha[:12]}…)"
                )
    return mismatches


def _check_skill_root(canonical_skill: Path, mirrors: tuple[Path, ...], relpaths: tuple[str, ...]) -> list[str]:
    mismatches: list[str] = []
    canonical_root = REPO_ROOT / canonical_skill
    canonical_hashes, canonical_errors = _hash_targets(canonical_root, relpaths)
    mismatches.extend(canonical_errors)
    for mirror in mirrors:
        for rel, canonical_sha in canonical_hashes.items():
            target = REPO_ROOT / mirror / rel
            if not target.exists():
                mismatches.append(f"missing mirror: {target}")
                continue
            actual_sha = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual_sha != canonical_sha:
                mismatches.append(
                    f"drift[skill]: {target} (expected sha256={canonical_sha[:12]}…, got {actual_sha[:12]}…)"
                )
    return mismatches


def main() -> int:
    reference_relpaths = (
        "venice-openapi.yaml",
        "venice-api-llms.md",
        "seedance-2-0-api-guide.md",
        "seedance-face-consent-api-guide.md",
        "request.schema.json",
    )
    skill_relpaths = ("SKILL.md",)
    skill_mirrors = (
        Path("adapters/kimi-code/venice-media"),
        Path("src/venice_media_skill/assets/skill"),
    )
    mismatches = _check_reference_subtree(
        canonical_dir=Path("references"),
        mirrors=skill_mirrors,
        relpaths=reference_relpaths,
    )
    mismatches.extend(
        _check_skill_root(
            canonical_skill=Path("skills/venice-media"),
            mirrors=skill_mirrors,
            relpaths=skill_relpaths,
        )
    )
    canonical_manifest = _tree_manifest(REPO_ROOT / "skills" / "venice-media")
    for mirror in skill_mirrors:
        mirror_manifest = _tree_manifest(REPO_ROOT / mirror)
        if mirror_manifest != canonical_manifest:
            missing = sorted(set(canonical_manifest) - set(mirror_manifest))
            extra = sorted(set(mirror_manifest) - set(canonical_manifest))
            changed = sorted(
                path
                for path in set(canonical_manifest) & set(mirror_manifest)
                if canonical_manifest[path] != mirror_manifest[path]
            )
            mismatches.append(f"tree drift[{mirror}]: missing={missing}, extra={extra}, changed={changed}")
    if mismatches:
        print(f"verify-bundled-assets: {len(mismatches)} drift item(s)", file=sys.stderr)
        for line in mismatches:
            print(f"  - {line}", file=sys.stderr)
        return 1
    print(f"verify-bundled-assets: references/ + SKILL.md synced across {len(skill_mirrors)} mirror(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
