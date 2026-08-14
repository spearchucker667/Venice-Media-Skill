# API synchronization

The bridge pins a reviewed Venice OpenAPI snapshot in `references/venice-openapi.yaml`. This document describes how to refresh that snapshot from the official upstream source.

## Source of truth

- **Upstream repository:** `https://github.com/veniceai/api-docs`
- **File of interest:** `swagger.yaml` at the repository root
- **Canonical local snapshot:** `references/venice-openapi.yaml`
- **Generated local schema:** `references/request.schema.json`

## One-time upstream checkout

```bash
UPSTREAM_DIR="$(mktemp -d)"
git clone --filter=blob:none https://github.com/veniceai/api-docs "$UPSTREAM_DIR/api-docs"
git -C "$UPSTREAM_DIR/api-docs" rev-parse HEAD
git -C "$UPSTREAM_DIR/api-docs" log -1 --date=iso-strict --format='%H%n%ad%n%s'
```

## Refresh the local snapshot

```bash
python scripts/sync-venice-api-docs.py "$UPSTREAM_DIR/api-docs" [--sha <exact-upstream-sha>]
```

The script:

1. Reads `swagger.yaml` from the upstream checkout.
2. Verifies the upstream SHA matches `--sha` when provided.
3. Records upstream provenance in the snapshot.
4. Writes `references/venice-openapi.yaml`.
5. Mirrors the updated references into all Skill trees.
6. Regenerates `references/request.schema.json` from runtime code.
7. Verifies idempotency: re-running against the same upstream SHA preserves
   the recorded `retrieved_utc` and produces byte-identical output.

After running it:

```bash
./scripts/validate.sh
```

## Ownership rules

- Only `scripts/sync-venice-api-docs.py` writes `references/venice-openapi.yaml`.
- Only `venice-media schema --output references/request.schema.json` regenerates the request schema.
- `scripts/verify-bundled-assets.py` enforces byte-identical mirrors.
- Hand-editing one mirror without updating the canonical source will fail CI.

## Drift detection

`.github/workflows/api-drift.yml` runs weekly and on `workflow_dispatch`. It checks the upstream OpenAPI version and relevant media request schemas against the tracked snapshot and fails if a meaningful drift is detected. It does not block ordinary pushes or pull requests.

## What to change after a refresh

If the refresh introduces new provider fields or operations:

1. Update typed operation definitions in `src/venice_media_skill/request.py` and `payloads.py`.
2. Add runner routing and output handling in `runner.py`.
3. Add or update tests, especially in `tests/test_api_contracts.py`.
4. Update `skills/venice-media/SKILL.md`, `docs/media-generation-guide.md`, and examples.
5. Run `./scripts/validate.sh`.
