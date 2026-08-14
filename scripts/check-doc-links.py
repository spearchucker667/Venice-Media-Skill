#!/usr/bin/env python3
"""Verify that relative Markdown links in tracked docs point to existing files.

Only local relative links are checked; absolute http(s) URLs are ignored so that
transient third-party outages do not break CI.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


def tracked_markdown_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "*.md"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return [root / line for line in result.stdout.splitlines() if line]


def find_links(text: str) -> list[tuple[str, str]]:
    # Markdown inline links [text](url) and reference-style [text]: url
    inline = re.findall(r"(?<!\!)\[([^\]]*)\]\(([^)]+)\)", text)
    reference = re.findall(r"^\[[^\]]+\]:\s+(\S+)", text, flags=re.MULTILINE)
    links = [(label, url) for label, url in inline]
    links.extend(("", url) for url in reference)
    return links


def resolve(root: Path, source: Path, url: str) -> Path | None:
    if url.startswith(("http://", "https://", "mailto:", "#")):
        return None
    target = root / url.lstrip("/") if url.startswith("/") else source.parent / url
    # Strip fragment and query.
    target = Path(str(target).split("#")[0].split("?")[0])
    return target


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    broken: list[tuple[Path, str, str]] = []
    for md_file in tracked_markdown_files(root):
        if not md_file.exists():
            # File may have been deleted but is still staged; skip it.
            continue
        text = md_file.read_text(encoding="utf-8")
        for label, url in find_links(text):
            target = resolve(root, md_file, url)
            if target is None:
                continue
            if not target.exists():
                broken.append((md_file.relative_to(root), label or "(ref)", url))
    if broken:
        print("Broken relative Markdown links:", file=sys.stderr)
        for source, label, url in broken:
            print(f"  {source}: [{label}]({url})", file=sys.stderr)
        return 1
    print("Documentation links: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
