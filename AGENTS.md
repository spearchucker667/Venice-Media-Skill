# Agent instructions

## Objective

Maintain a host-neutral, public-ready Venice media bridge. The host agent (Kimi, Codex, Claude Code, Gemini CLI, OpenCode, …) remains the primary reasoning system; this Python bridge performs deterministic API operations as a subprocess.

The bridge never runs an LLM loop, never calls Venice chat completions, and never replaces the host agent.

## Required validation

Before any commit, run:

```bash
./scripts/validate.sh
```

This is the single source of truth for CI quality and runs, in order:
`python -m compileall -q src` → `ruff check .` → `ruff format --check .` → `mypy src` → `pytest --cov=venice_media_skill` → `python -m build` → `validate-openapi references/venice-openapi.yaml` → JSON sanity check on `adapters/kimi-code/kimi.plugin.json` and `references/request.schema.json`.

CI also runs this on Python 3.11 / 3.12 / 3.13 and a cross-platform smoke (`macos-latest`, `windows-latest`) that exercises `doctor`, `schema`, and `validate-openapi`.

## Targeted commands

| Need | Command |
| --- | --- |
| Full validation | `./scripts/validate.sh` |
| Lint / format | `python -m ruff check .` / `python -m ruff format --check .` |
| Types (strict, `src/` only) | `python -m mypy src` |
| Tests | `python -m pytest` |
| Coverage report | `python -m pytest --cov=venice_media_skill --cov-report=term-missing` |
| Single test | `python -m pytest tests/test_security.py::TestReservedParameterRejection -q` |
| OpenAPI snapshot check | `python -m venice_media_skill validate-openapi references/venice-openapi.yaml` |
| Rebuild & reinstall locally | `python -m pip install -e '.[dev]'` |
| Build wheel + sdist | `python -m build` |

Coverage gate is 80% (`tool.coverage.report.fail_under` in `pyproject.toml`).

## Repo layout

```
src/venice_media_skill/   Python bridge package (mypy strict target)
  cli.py                  argparse entry; JSON stdout, errors stderr, exit codes 0/2-9
  client.py               Bearer-authenticated HTTPS + fail-closed public downloader
  catalog.py              Live GET /models with 1h on-disk cache
  config.py               platformdirs paths; Settings.load(require_api_key=…)
  consent.py              ConsentStore + QuoteApprovalStore (hash-bound, single-use)
  errors.py               Typed error hierarchy
  installer.py            install the Skill bundle to host-agent directories
  jobs.py                 Durable local queue records (resume, never auto-resubmit)
  output.py               Atomic writes, binary decoding, metadata sidecars
  payloads.py             Single authority for provider bodies; reserved-key gating
  planner.py              Model-aware question groupings + image defaults
  request.py              Manifest parsing + JSON Schema
  reserved.py             RESERVED_PARAMETERS / RESERVED_PROVIDER_KEYS
  runner.py               Operation dispatch + quote gate + queue polling
  util.py                 fast_validate_content_type, redact_data, slug helpers
  assets/skill/           Vendored Skill bundle shipped via wheel
adapters/                 Per-host plugin entries (kimi-code, generic)
skills/venice-media/      Source-of-truth Skill (mirrors into assets/skill)
references/               Bundled API references (do NOT regenerate silently)
  venice-openapi.yaml     Reviewed OpenAPI snapshot — preserve provenance
  venice-api-llms.md      LLM-readable Venice API snapshot
  request.schema.json     Generated from request.request_json_schema()
  seedance-2-0-api-guide.md
  seedance-face-consent-api-guide.md
tests/                    Offline test suite (no live Venice calls)
scripts/                  install.sh, install.ps1, uninstall.sh, refresh-openapi.sh, validate.sh
docs/                     architecture, threat-model, agent-workflow, host-integrations…
```

## Conventions

- **Python 3.11+** (`requires-python = ">=3.11"`). Build backend: hatchling.
- **Editable install for development**: `python -m pip install -e '.[dev]'` is required so `pytest`, `mypy`, and the `venice-media` console script resolve against the source tree.
- **`mypy` packages = `["venice_media_skill"]` only.** Tests are not under strict mypy; do not add them.
- **Ruff rules**: `E, F, I, B, UP, SIM, RUF`. Line length 120. Per-file ignore for `tests/*`: `S101`, `B017`.
- **No `pre-commit`, no Node, no Docker.** Keep edits minimal and idiomatic to this repo.
- **JSON to stdout, diagnostics to stderr.** Exit codes: `0` ok, `2` generic error, `3` API error, `4` consent required, `5` consent approval required, `6` quote approval required, `7` network safety, `8` quote approval mismatch, `9` transport error.

## Invariants — DO NOT REGRESS

The following are hard-wired by the source, tests, and threat model. Treat any change here as a security change, not a refactor.

- Output the **complete corrected file** whenever modifying a repository artifact (no partial diffs).
- **Never** add secrets or credential storage. `VENICE_API_KEY` is read only from the environment; the bridge never writes it to disk, logs, manifests, or queue records.
- Preserve **live model discovery**: query `GET /models`; do not hard-code model catalogs.
- Preserve image defaults in `planner.py` / `payloads.py`: `safe_mode=false`, `hide_watermark=true`.
- Preserve explicit **Seedance face-consent** confirmation: the runner persists a hash-bound `ConsentChallenge` from a `409 needs_consent`; it attaches `consents.seedance` **only** after the host invokes `venice-media approve-consent <challenge_id> --acknowledge-policy --max-cost <USD>`. The runner must never auto-resubmit.
- Preserve **quote gating** for `video.generate` and `audio.generate` (`runner.QUOTE_REQUIRED_OPERATIONS`): approve via `venice-media approve-quote <op> <payload_hash> --quote <file> --max-cost <USD>`; single-use, hash-bound, max-cost enforced.
- Preserve **timeout-safe queue recovery**. On poll timeout, the runner returns the `queue_id`; the host retrieves via `video.retrieve` / `audio.retrieve` with `parameters.queue_id`. Never auto-resubmit paid queued jobs.
- Do **not** forward `Authorization` to download URLs. `client.py` uses a fresh unauthenticated `httpx.Client` per hop, an allow-list (`ALLOWED_DOWNLOAD_HOSTS` + `ALLOWED_HOST_SUFFIXES`), manual redirect walking (≤5 hops), HTTPS-only, non-global-IP rejection, and fail-closed DNS.
- Streamed downloads enforce both `Content-Length` pre-flight and an in-flight byte cap (`DEFAULT_PUBLIC_MAX_BYTES`) with SHA-256 in flight. Magic-byte validation (`util.fast_validate_content_type`) is fail-closed; decoded base64/JSON artifacts are revalidated.
- Reserved / transport keys (`consents`, `model`, `prompt`, `queueId`, `download_url`/`downloadUrl`, `image_url`/`imageUrl`, `Authorization`, `api_key`, `stream`, …) are **rejected** inside `parameters` by `payloads.assert_no_reserved_parameters` → `ReservedParameterError`. Quote and queue payloads come from the same canonical hash so the gate is uniform.
- Update **tests and documentation** with any implementation change. PRs follow `CONTRIBUTING.md`'s checklist.
- Keep the bundled `references/venice-openapi.yaml` provenance intact: it is a reviewed snapshot, not a regenerated artifact. The CLI resolves it from `references/` (editable) or `venice_media_skill/assets/skill/references/` (installed wheel).

## Operational quirks

- The CLI requires `VENICE_API_KEY` from env for any non-`dry_run` request; `dry_run: true` accepts a placeholder key. Set `--online` only with a real key.
- State directories live under `platformdirs` user dirs (`~/.config`, `~/.cache`, `~/.local/state/venice-media-skill`); output defaults to `./venice-media-output/`. CI overrides via `VENICE_MEDIA_CONFIG_DIR`, `VENICE_MEDIA_CACHE_DIR`, `VENICE_MEDIA_STATE_DIR`, `VENICE_MEDIA_OUTPUT_DIR`.
- Quote and consent stores are JSON files in `state_dir/`: `consent_approvals.json`, `quote_approvals.json`. They are append-and-overwrite by design; do not diff-treat them as ephemeral.
- `./scripts/install.sh --host kimi --scope user` creates an isolated venv at `~/.local/share/venice-media-skill/venv` and a launcher at `~/.local/bin/venice-media` (Windows: `install.ps1`). Ensure `~/.local/bin` is on `PATH`.
- Skill assets are mirrored: changes in `skills/venice-media/` must reach `src/venice_media_skill/assets/skill/` (shipped via the wheel); treat them as a paired edit.
- `.env*`, `*.key`, `*.pem`, queue records, and generated media are git-ignored — never loosen this.
- `references/request.schema.json` is generated from `request.request_json_schema()` (see `cli.py:schema`); regenerate it alongside request-shape changes.

## Pointers for deeper work

- Architecture and trust zones: `docs/architecture.md`.
- Threat model: `docs/threat-model.md` and `docs/security-and-privacy.md`.
- Per-operation field guidance: `docs/media-generation-guide.md`.
- Per-host setup (Kimi / Codex / …): `docs/host-integrations.md`.
- Release flow (git tag `v*` → `softprops/action-gh-release@v3` + zip/tar.gz/sha256): `docs/releasing.md` and `.github/workflows/release.yml`.
