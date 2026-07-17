#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$ROOT/references/venice-openapi.yaml"
TEMP="$(mktemp)"
trap 'rm -f "$TEMP"' EXIT

curl --fail --silent --show-error --location \
  'https://api.venice.ai/doc/api/swagger.yaml' \
  --output "$TEMP"

python - "$TEMP" <<'PY'
import pathlib, sys, yaml
path = pathlib.Path(sys.argv[1])
payload = yaml.safe_load(path.read_text())
assert payload.get('openapi') == '3.0.0'
assert '/image/generate' in payload.get('paths', {})
assert '/video/queue' in payload.get('paths', {})
print(payload.get('info', {}).get('version'))
PY

cp "$TEMP" "$TARGET"
echo "Updated $TARGET. Review the diff, provenance, and request-schema compatibility before committing."
