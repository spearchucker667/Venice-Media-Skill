#!/usr/bin/env python3
"""Fail release builds whose tag, package version, or changelog disagree."""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2 or not re.fullmatch(r"v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", sys.argv[1]):
        print("usage: verify-release.py v<semantic-version>", file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parent.parent
    tag_version = sys.argv[1][1:]
    package = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    package_version = package["project"]["version"]
    if tag_version != package_version:
        print(f"release tag {tag_version!r} does not match package version {package_version!r}", file=sys.stderr)
        return 1
    version_sources = {
        "src/venice_media_skill/__init__.py": re.search(
            r'^\s*__version__\s*=\s*["\']([^"\']+)["\']',
            (root / "src/venice_media_skill/__init__.py").read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        ),
        "adapters/kimi-code/kimi.plugin.json": json.loads(
            (root / "adapters/kimi-code/kimi.plugin.json").read_text(encoding="utf-8")
        ).get("version"),
        "src/venice_media_skill/assets/kimi.plugin.json": json.loads(
            (root / "src/venice_media_skill/assets/kimi.plugin.json").read_text(encoding="utf-8")
        ).get("version"),
    }
    for source, value in version_sources.items():
        resolved = value.group(1) if isinstance(value, re.Match) else value
        if resolved != tag_version:
            print(f"release tag {tag_version!r} does not match {source} version {resolved!r}", file=sys.stderr)
            return 1
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    if not re.search(rf"^## .*\[{re.escape(tag_version)}\]", changelog, flags=re.MULTILINE):
        print(f"CHANGELOG.md has no release section for {tag_version}", file=sys.stderr)
        return 1
    print(
        f"release metadata: tag={tag_version}, package={package_version}, "
        "runtime/plugin versions=matched, changelog=present"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
