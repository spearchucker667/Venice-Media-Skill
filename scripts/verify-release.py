#!/usr/bin/env python3
"""Fail release builds whose tag, package version, or changelog disagree."""

from __future__ import annotations

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
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    if not re.search(rf"^## .*\[{re.escape(tag_version)}\]", changelog, flags=re.MULTILINE):
        print(f"CHANGELOG.md has no release section for {tag_version}", file=sys.stderr)
        return 1
    print(f"release metadata: tag={tag_version}, package={package_version}, changelog=present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
