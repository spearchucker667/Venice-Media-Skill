#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python -m compileall -q src
python -m ruff check .
python -m ruff format --check .
python -m mypy src
python -m pytest --cov=venice_media_skill --cov-report=term-missing
python -m build
PYTHONPATH=src python -m venice_media_skill validate-openapi references/venice-openapi.yaml
python - <<'PY'
import json
from pathlib import Path
json.loads(Path('adapters/kimi-code/kimi.plugin.json').read_text())
json.loads(Path('references/request.schema.json').read_text())
print('JSON assets: OK')
PY
python scripts/verify-bundled-assets.py
python scripts/inspect-sdist.py
