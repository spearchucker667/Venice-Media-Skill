# Venice Media Skill remediation audit — 2026-07-18

## Scope and baseline

- Checkout: `/Users/super_user/Projects/CLI venice media creation`
- Baseline commit: `f8454ff894ec72fc7b1bccc3072d50b00eefd4af`
- Pre-existing work preserved: modified `AGENTS.md`
- Baseline command: `python3 -m venv .venv && python -m pip install -e '.[dev]' && bash scripts/validate.sh`
- Baseline result: exit 0; 253 passed, 3 skipped; 83.19% coverage; lint, format, mypy, build, schema drift, OpenAPI path check, mirror check, and sdist inspection passed.
- Important baseline caveat: the existing tests asserted several incomplete implementations as fixed. This audit reproduced findings against source behavior rather than relying on commit messages or test names.

## Finding checklist

### VMS-001 — streamed artifact metadata

- [x] Reproduced against current checkout.
- Reproduction result: superseded by the existing implementation; `_download_with_sink()` propagates `file_path`, `sha256`, and `observed`.
- Root cause: historical omission of `finalized.observed`.
- Files changed: none in this tranche.
- Tests: existing file-backed response and queue artifact integration coverage retained.
- Validation result: full suite passes.
- Status: false positive against current HEAD / previously fixed.

### VMS-002 — Seedance challenge persistence

- [x] Reproduced: confirmed. The client raised `ConsentRequired` while runner code expected an `ApiResponse`.
- Root cause: incompatible exception/response contracts across client and runner.
- Files changed: `runner.py`.
- Tests: existing consent tests plus real runner path retained.
- Validation result: mocked 409 is converted only inside the runner, persisted, and never retried.
- Status: fixed.

### VMS-003 — operation-discriminated JSON Schema

- [x] Reproduced: confirmed. Base `parameters.additionalProperties=false` rejected all nonempty parameter objects and branches did not require model/prompt/duration/queue ID.
- Root cause: intersecting base and conditional schemas were treated as overrides.
- Files changed: `request.py`, all `request.schema.json` mirrors, example tests.
- Tests: every shipped example validates and reaches the real dry-run CLI; missing operation requirements and strict operation parameter shapes are covered.
- Validation result: meta-valid, behavioral tests pass, drift is zero.
- Status: fixed.

### VMS-004 — WAV/WEBP and content validation

- [x] Reproduced: confirmed. RIFF size bytes were compared to zero; JSON was not parsed; text NULs were accepted.
- Root cause: fixed-byte signatures were used for variable RIFF headers and documentation exceeded implementation.
- Files changed: `util.py`, `test_audit_remediation.py`.
- Tests: realistic nonzero RIFF sizes, malformed JSON, and NUL-bearing text.
- Validation result: targeted and full tests pass.
- Status: fixed.

### VMS-005 — duplicated operation contracts

- [x] Reproduced: confirmed request and builder allowlists disagreed.
- Root cause: independent allowlists in request parsing and payload projection.
- Files changed: `request.py`, `payloads.py`, examples, README, Skill mirrors.
- Tests: shipped examples traverse schema, parser, builder, and CLI; strict shape tests pass.
- Validation result: payload builders now consume `allowed_parameter_names()` from the request contract authority.
- Status: fixed for executable parameter contracts; documentation is synchronized.

### VMS-006 — planner instrumental field

- [x] Reproduced: confirmed planner emitted `parameters.instrumental`.
- Root cause: test encoded a stale alias.
- Files changed: `planner.py`, `test_planner.py`, `CHANGELOG.md`.
- Tests: planner expects `parameters.force_instrumental`.
- Validation result: planner suite passes.
- Status: fixed.

### VMS-007 — non-finite numbers

- [x] Reproduced: confirmed for execution/config/store and quote parsing paths.
- Root cause: Python float and JSON defaults admit NaN/infinity; comparisons with NaN fail open.
- Files changed: `request.py`, `config.py`, `consent.py`, `cli.py`, tests.
- Tests: non-finite execution values, strict quote JSON, finite/nonnegative store validation.
- Validation result: non-finite and boolean-as-number values are rejected before persistence or comparison.
- Status: fixed.

### VMS-008 — consent approval lifecycle

- [x] Reproduced: confirmed approvals were reusable and `max_cost` was ignored.
- Root cause: read-only `approval_for()` was used at submission.
- Files changed: `consent.py`, `runner.py`.
- Tests: existing approval tests plus full suite.
- Validation result: matching approval is atomically consumed once and observed quote cost is enforced.
- Status: fixed.

### VMS-009 — transport exit code

- [x] Reproduced: confirmed client wrapped transport errors in `ApiError(status_code=0)`.
- Root cause: exception normalization erased transport semantics.
- Files changed: `errors.py`, `client.py`, `cli.py`, tests.
- Tests: client typed exception and CLI exit code 9.
- Validation result: provider HTTP errors remain 3; transport errors are 9.
- Status: fixed.

### VMS-010 — cross-filesystem output commit

- [x] Reproduced: confirmed staging ignored request output and `EXDEV` was not handled.
- Root cause: staging and final paths could be on different filesystems.
- Files changed: `runner.py`, `output.py`.
- Tests: file-blob integration retained; EXDEV fallback validates size and SHA before atomic replacement.
- Validation result: normal path and fallback pass source validation.
- Status: fixed.

### VMS-011 — ignored execution fields

- [x] Reproduced: confirmed dead `pass` and misleading bypass field.
- Root cause: obsolete manifest surface survived mandatory quote-gate redesign.
- Files changed: `request.py`.
- Tests: full request suite.
- Validation result: non-default `skip_quote=true` is rejected explicitly; `quote_first` and `confirmed_cost` remain documented compatibility/informational fields.
- Status: fixed without weakening the quote gate.

### VMS-012 — atomic helper return path

- [x] Reproduced: confirmed helpers returned the renamed-away temp path.
- Root cause: return value was not updated after `os.replace`.
- Files changed: `output.py`, tests.
- Tests: returned path equals and resolves to the existing target.
- Validation result: passes.
- Status: fixed.

### VMS-013 — concurrent state writes

- [x] Reproduced: confirmed fixed `.tmp` paths and unlocked job read-modify-write.
- Root cause: atomic rename was used without unique temps or transaction locks.
- Files changed: `jobs.py`, `catalog.py`.
- Tests: full state suite; state writes use unique sibling temps through `atomic_write_text` and exclusive per-record locks.
- Validation result: passes locally.
- Status: fixed; high-contention multiprocessing stress remains a CI follow-up.

### VMS-014 — lock hardening

- [x] Reproduced: confirmed global `/tmp`, permissive creation, malformed-lock deletion race, and ineffective shared reads.
- Root cause: home-grown lock publication and stale recovery.
- Files changed: `consent.py`, tests.
- Tests: hashed paths and stale recovery retained.
- Validation result: per-user state directory, 0700 directory, 0600 lock files, age-gated malformed recovery, and exclusive read/write locking.
- Status: fixed for identified defects; custom locking remains less battle-tested than a dedicated dependency.

### VMS-015 — destructive/inconsistent installers

- [x] Reproduced: confirmed all three entrypoints deleted destinations first and scripts installed generic+Kimi for `kimi`.
- Root cause: independent installer implementations and remove-then-copy updates.
- Files changed: `installer.py`, `install.sh`, `install.ps1`, new `uninstall.ps1`.
- Tests: Python host-selection suite; scripts use the same exclusive host semantics and staging/rollback algorithm.
- Validation result: Python tests pass; Windows script execution requires Windows CI.
- Status: fixed locally; cross-platform script execution is CI-dependent.

### VMS-016 — configuration names and endpoint override

- [x] Reproduced: confirmed README used `VENICE_API_BASE` while code used `VENICE_BASE_URL`, and CLI lacked opt-in.
- Root cause: documentation drift and a constructor-only development flag.
- Files changed: `README.md`, `cli.py`, `config.py`.
- Tests: config suite.
- Validation result: one variable name, explicit `--allow-noncanonical-endpoint`, finite timeout, supported config-key validation.
- Status: fixed.

### VMS-017 — OpenAPI validation

- [x] Reproduced: confirmed path-only validation and one YAML boolean-coercion defect in the reviewed snapshot.
- Root cause: no OpenAPI validator and unquoted `off`/`on` enum values.
- Files changed: `cli.py`, `pyproject.toml`, OpenAPI snapshot plus all mirrors.
- Tests: CLI validation and invalid-spec tests.
- Validation result: maintained validator passes; provenance records the semantics-preserving local quoting correction.
- Status: fixed.

### VMS-018 — examples not executed

- [x] Reproduced: confirmed image example parsed but builder rejected width/height before contract alignment.
- Root cause: tests stopped after parsing.
- Files changed: image example, README, Skill mirrors, integration tests.
- Tests: enumerate every example, substitute real temp fixtures, schema validate, parse/build, execute CLI dry-run, assert no online path.
- Validation result: passes.
- Status: fixed.

### VMS-019 — bundled OpenAPI temp leak

- [x] Reproduced: confirmed `mkstemp` path had no unlink.
- Root cause: incorrect assumption that process exit removes arbitrary temp files.
- Files changed: `cli.py`.
- Tests: installed-artifact smoke exercises bundled resolution.
- Validation result: fallback temp is registered for process-exit unlink.
- Status: fixed; a future refactor can remove the copy entirely by validating resource text.

### VMS-020 — large local inputs

- [x] Reproduced: confirmed whole-file hashing and unaccounted Base64 expansion.
- Root cause: convenience `read_bytes()` helpers and raw-size-only limits.
- Files changed: `util.py`, `payloads.py`, `runner.py`.
- Tests: source suite and existing data-URL tests.
- Validation result: hashes stream in chunks; Base64 expansion is checked against the 35 MiB JSON-body ceiling with URL guidance.
- Status: fixed for hashing and queue-body sizing; Base64 encoding itself necessarily materializes the encoded request body.

### VMS-021 — content-validation claims

- [x] Reproduced: confirmed JSON/NUL/RIFF mismatches; Ogg aliases were already handled.
- Root cause: signature table and prose diverged.
- Files changed: `util.py`, tests.
- Tests: realistic RIFF, parsed JSON, NUL rejection, existing MIME corpus.
- Validation result: passes.
- Status: fixed; raw PCM remains deliberately limited to explicit `audio/pcm` policy checks.

### VMS-022 — lower-bound dependency CI

- [x] Reproduced: confirmed `jsonschema` was omitted.
- Root cause: runtime dependency list and CI pins were maintained separately.
- Files changed: `ci.yml`.
- Tests: CI now pins jsonschema and the OpenAPI validator and runs `pip check`.
- Validation result: YAML inspected locally; hosted CI not run in this uncommitted workspace.
- Status: fixed in workflow, pending hosted CI.

### VMS-023 — installed-artifact coverage

- [x] Reproduced: confirmed editable cross-platform smoke and Linux-only wheel/sdist coverage.
- Root cause: source-checkout tests substituted for artifact tests.
- Files changed: `ci.yml`.
- Tests: macOS/Windows build+wheel install smoke; Linux clean wheel and sdist venv smoke, bundled assets, schema, console script.
- Validation result: local wheel/sdist validation pending final matrix; hosted Windows/macOS pending CI.
- Status: fixed in workflow, pending hosted CI.

### VMS-024 — security scanning

- [x] Reproduced: confirmed no dependency/source scanning job.
- Root cause: quality gate omitted network-enabled audit tooling.
- Files changed: `pyproject.toml`, `ci.yml`.
- Tests: Bandit, pip-audit, pip check job.
- Validation result: final local audit recorded separately; dependency results remain time-dependent.
- Status: fixed in workflow, pending hosted CI.

### VMS-025 — release/version consistency

- [x] Reproduced: confirmed any `v*` tag could publish version 0.1.0.
- Root cause: tag and package metadata were independent.
- Files changed: new `verify-release.py`, `release.yml`.
- Tests: script rejects malformed/mismatched tags and requires changelog section.
- Validation result: `v0.1.0` passes locally.
- Status: fixed.

### VMS-026 — Actions controls

- [x] Evaluated.
- Reproduction result: confirmed missing concurrency and timeouts; broad release write permission remains job-scoped by workflow.
- Root cause: minimal initial workflows.
- Files changed: `ci.yml`, `release.yml`.
- Tests: workflow syntax and final validation.
- Validation result: concurrency cancellation and job timeouts added.
- Status: partially fixed; immutable action SHA pinning and attestations are deferred until compatible SHAs are verified in a networked workflow update.

### VMS-027 — mirror verification

- [x] Reproduced: confirmed selected-file-only checks missed extras and omitted assets.
- Root cause: hard-coded subset comparison.
- Files changed: `verify-bundled-assets.py`.
- Tests: deterministic relative-path, size, SHA-256 full-tree manifests.
- Validation result: exact canonical/mirror trees pass.
- Status: fixed.

### VMS-028 — archive hygiene

- [x] Reproduced: generated caches exist locally but `git ls-files` confirms they are untracked/ignored.
- Root cause: filesystem ZIPs include ignored local state.
- Files changed: new `export-source.sh`.
- Tests: sdist inspector and git tracked-cache scan.
- Validation result: export uses `git archive`, emits SHA-256, and defaults to Desktop without including ignored files.
- Status: fixed; caches were not deleted because they are active validation outputs and not tracked release inputs.

## Residual risks

1. Hosted Python 3.11/3.12/3.13, Windows, and macOS jobs cannot be claimed green until this uncommitted work is pushed and CI runs.
2. VMS-026 immutable action SHA pinning and build attestations remain deferred pending verified compatible SHAs.
3. The custom cross-platform lock has materially safer semantics but still warrants sustained multiprocessing stress on all supported operating systems.
4. Network vulnerability results are point-in-time; a local clean audit does not guarantee future dependency safety.

## Final validation evidence

- `bash scripts/validate.sh`: exit 0.
- Tests: 260 passed, 3 Windows-specific skips; branch coverage 82.49% (80% required).
- Ruff lint/format: pass; mypy strict for all 17 source files: pass.
- Build: `venice_media_skill-0.1.0-py3-none-any.whl` and `venice_media_skill-0.1.0.tar.gz`.
- Wheel SHA-256: `627128b58f03caf6188efa57978e21c444b1248f812c28b711f4fb25a36bc10b`.
- Sdist SHA-256: `40e692514b2534ec2c0cb34d4afd37f3a6c58949ef1431383c9b25dc939ea2e3`.
- Fresh wheel and sdist environments: console help, schema, bundled OpenAPI validation, and temp-file cleanup passed outside the checkout.
- `python -m bandit -q -r src`: exit 0.
- `python -m pip_audit`: exit 0, no known vulnerabilities found; the local editable project itself is not a PyPI dependency and was skipped by name.
- `python -m pip check`: exit 0, no broken requirements.
- `python scripts/verify-release.py v0.1.0`: exit 0.
- `git diff --check`: exit 0.
