# Contributing

## Ground rules

- Do not commit API keys, generated personal media, local queue records, or `.env` files.
- Treat the bundled OpenAPI document as a reviewed snapshot; preserve provenance.
- Do not hard-code a static model catalog when live `/models` data can drive behavior.
- Do not weaken consent, quote, redaction, or duplicate-spend protections.
- Keep stdout JSON-compatible and send operational diagnostics to stderr.
- Add tests for every behavior change.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
./scripts/validate.sh
```

## Pull requests

Include:

- Problem statement
- Evidence or API reference
- Behavior before and after
- Tests added or changed
- Security/privacy impact
- Manual validation commands

Changes to request payloads should include a dry-run example and a corresponding OpenAPI reference.
