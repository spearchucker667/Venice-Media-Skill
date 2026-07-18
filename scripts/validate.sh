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
import json, jsonschema, subprocess, sys, tempfile
from pathlib import Path

# 1. JSON parse sanity for the bundled assets.
json.loads(Path('adapters/kimi-code/kimi.plugin.json').read_text())
json.loads(Path('references/request.schema.json').read_text())
print('JSON assets: OK')

# 2. Meta-validation of the bundled request schema.
from venice_media_skill.request import request_json_schema
schema = request_json_schema()
jsonschema.Draft202012Validator.check_schema(schema)

# 3. Drift check: regenerate the schema into a tmpdir and compare the
#    committed file byte-for-byte. A mismatch means the committed
#    references/request.schema.json is out of sync with the runtime.
with tempfile.TemporaryDirectory() as tmp:
    regen = Path(tmp) / "request.schema.json"
    subprocess.run(
        [sys.executable, "-m", "venice_media_skill", "schema", "--output", str(regen)],
        check=True,
        env={"PYTHONPATH": "src", "PATH": "/usr/bin:/bin"},
        stdout=subprocess.DEVNULL,
    )
    on_disk = json.loads(Path('references/request.schema.json').read_text())
    regen_loaded = json.loads(regen.read_text())
    if on_disk != regen_loaded:
        sys.stderr.write("request.schema.json drift detected - run: python -m venice_media_skill schema --output references/request.schema.json\n")
        sys.exit(1)
    print("request.schema.json: in sync with runtime")
print('Schema: meta-valid, in-sync, drift=0')
PY
python scripts/verify-bundled-assets.py
python scripts/inspect-sdist.py
