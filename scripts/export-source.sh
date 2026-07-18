#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VERSION="$(python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
OUTPUT="${1:-$HOME/Desktop/venice-media-skill-${VERSION}-source.zip}"
mkdir -p "$(dirname "$OUTPUT")"
git archive --format=zip --prefix="venice-media-skill-${VERSION}/" --output="$OUTPUT" HEAD
python - "$OUTPUT" <<'PY'
import hashlib
import pathlib
import sys

path = pathlib.Path(sys.argv[1]).resolve()
digest = hashlib.sha256(path.read_bytes()).hexdigest()
print(f"{digest}  {path}")
PY
