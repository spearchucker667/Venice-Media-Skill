#!/usr/bin/env python3
"""Extract the CHANGELOG.md section for a release tag into a release body.

Usage:
    python scripts/extract-changelog-notes.py v1.3.1 [--output release-notes.md]

The extracted section becomes the GitHub Release body. The release workflow
additionally appends GitHub-generated contributor/PR notes.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def extract(tag: str, changelog: Path) -> str:
    version = tag[1:] if tag.startswith("v") else tag
    text = changelog.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"^## \s*\[?{re.escape(version)}\]?\b[^\n]*\n(?P<body>.*?)(?=^## )",
        flags=re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    if match is None:
        raise SystemExit(f"extract-changelog-notes: no CHANGELOG.md section for {version}")
    body = match.group("body").strip()
    if not body:
        raise SystemExit(f"extract-changelog-notes: CHANGELOG.md section for {version} is empty")
    return body + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract a changelog section as release notes.")
    parser.add_argument("tag", help="Release tag, e.g. v1.3.1")
    parser.add_argument("--output", type=Path, help="Write the body to this file instead of stdout")
    parser.add_argument(
        "--changelog",
        type=Path,
        default=REPO_ROOT / "CHANGELOG.md",
        help="Path to CHANGELOG.md",
    )
    args = parser.parse_args()
    body = extract(args.tag, args.changelog)
    if args.output:
        args.output.write_text(body, encoding="utf-8")
    else:
        sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
