#!/usr/bin/env python3
"""Verify every artifact listed in a GitHub Release SHA256SUMS.txt file."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path, PurePosixPath

CHECKSUM_LINE = re.compile(r"^([0-9a-fA-F]{64}) [ *](.+)$")


def _safe_artifact_path(bundle: Path, name: str) -> Path:
    normalized = name.replace("\\", "/")
    relative = PurePosixPath(normalized)
    if relative.is_absolute() or ".." in relative.parts or not relative.name:
        raise ValueError(f"unsafe artifact path: {name!r}")
    return bundle.joinpath(*relative.parts)


def verify_release_assets(bundle: Path, checksum_name: str = "SHA256SUMS.txt") -> list[tuple[str, str]]:
    checksum_path = bundle / checksum_name
    if not checksum_path.is_file():
        raise ValueError(f"checksum file missing: {checksum_path}")

    verified: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(checksum_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        match = CHECKSUM_LINE.fullmatch(raw_line)
        if match is None:
            raise ValueError(f"invalid checksum line {line_number}: {raw_line!r}")
        expected, name = match.groups()
        if name in seen:
            raise ValueError(f"duplicate checksum entry: {name}")
        seen.add(name)
        artifact = _safe_artifact_path(bundle, name)
        if not artifact.is_file():
            raise ValueError(f"artifact missing: {name}")
        actual = hashlib.sha256(artifact.read_bytes()).hexdigest()
        if actual.lower() != expected.lower():
            raise ValueError(f"checksum mismatch: {name} expected={expected.lower()} actual={actual}")
        verified.append((actual, name))

    if not verified:
        raise ValueError("checksum file contains no artifact entries")
    return verified


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", nargs="?", default=".", help="Directory containing release assets")
    parser.add_argument("--checksum-file", default="SHA256SUMS.txt", help="Checksum filename inside the bundle")
    args = parser.parse_args(argv)

    try:
        verified = verify_release_assets(Path(args.bundle), args.checksum_file)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"verify-release-assets: error: {exc}", file=sys.stderr)
        return 1

    for digest, name in verified:
        print(f"verified {digest} {name}")
    print(f"verify-release-assets: ok ({len(verified)} artifacts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
