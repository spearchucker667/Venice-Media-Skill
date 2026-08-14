# Audit Remediation Report

Repository: https://github.com/spearchucker667/Venice-Media-Skill
Baseline commit: `2776f7130e64db6ff030fe17fb9477fd876c5f81` (`fix: close media audit defects`)
Remediation commit: `e539a608d3b0b51c4d6974b7c19c9c2a44d811a6`
Date: 2026-08-14

---

## 1. Initial State

- Worktree: **clean** (`git status --short --branch` showed no uncommitted changes before edits).
- Branch: `main`, synchronized with `origin/main` (`0 0` ahead/behind).
- Local Python: `3.12.11` (development venv), `3.14.6` (system default).
- `gh` authenticated as `spearchucker667` with `repo` and `workflow` scopes.

## 2. CI Baseline and Root Cause

### Observed failure

GitHub Actions run `29658212712` (2026-07-18, commit `2776f71`) failed on **every job** within ~5–6 seconds. Job logs were no longer retained by GitHub (`log not found`), so the failure class is inferred from the run metadata and workflow history: all jobs aborted during action bootstrap before any Python step executed.

### Root cause

The workflow referenced `actions/checkout@v7` and `actions/setup-python@v6`. At the time of the July 18 run, `actions/checkout@v7` did not yet exist. The action resolution failure aborted all matrix jobs before checkout.

### Remediation

- Updated CI and Release workflows to stable, currently supported versions:
  - `actions/checkout@v6` (proven on the current runner fleet)
  - `actions/setup-python@v6` (proven on the current runner fleet)
- Pinned `softprops/action-gh-release` to the immutable commit SHA `c12583777ecdfd3be55c69cf75464299dc01057e`.
- Added Dependabot `ignore` rules for `version-update:semver-major` on first-party Actions so major-version bumps require explicit verification.
- Added a `Bootstrap diagnostics` step to every job (Python/pip/platform versions) and gave all validation commands explicit step names.

## 3. Bugs Found and Fixed

### P0-1: GitHub Actions bootstrap failure

- **Files changed:** `.github/workflows/ci.yml`, `.github/workflows/release.yml`, `.github/dependabot.yml`
- **Fix:** Downgraded first-party Actions to v6, SHA-pinned third-party release action, added major-version ignore policy, added bootstrap diagnostics.
- **Regression coverage:** Verified by the green CI run `31765342191`.

### P0-2: mypy strict unreachable-code failure on Linux CI

- **Files changed:** `pyproject.toml`, `src/venice_media_skill/keychain.py`
- **Fix:** `keychain.py` is macOS-only; on Linux CI, mypy 2.3.0 with `warn_unreachable=true` flagged the Darwin-only path as unreachable. Factored the macOS logic into `_run_keychain()` and added a targeted mypy override `warn_unreachable = false` for that module only. Global strictness is unchanged.
- **Regression coverage:** CI Quality jobs now pass `mypy` on Python 3.11/3.12/3.13.

### P1-1: Content-routing contract underspecified

- **Files changed:** `skills/venice-media/SKILL.md`, `src/venice_media_skill/assets/skill/SKILL.md`, `adapters/kimi-code/venice-media/SKILL.md`, `adapters/generic/AGENT_INSTRUCTIONS.md`, `tests/test_audit_remediation.py`
- **Fix:** Replaced the ambiguous `Do not bypass provider or platform policy failures` with an explicit **host-vs-Venice policy separation** rule. Added a dedicated `### Content-routing semantics` section covering:
  - explicit Venice invocation;
  - native-generator restrictions must not be attributed to Venice;
  - no silent `safe_mode=true`, family-safe filtering, or prompt sanitization;
  - adult/provider-permitted prompts are preserved faithfully;
  - no invented Venice refusals;
  - actual Venice responses are reported accurately;
  - host-layer restrictions are identified as host-layer restrictions;
  - `VENICE_API_KEY` is authentication, not a policy override.
- **Regression tests added:**
  - `test_content_routing_section_present_in_all_skill_mirrors`
  - `test_no_stale_bypass_wording_in_skill_or_adapters`
  - `test_host_venice_policy_layers_are_separate`
  - `test_adult_prompt_preserved_without_artificial_refusal`
  - `test_adapters_do_not_enable_safe_mode_or_sanitization`
  - `test_skill_activation_is_explicit`
  - `test_consent_and_quote_language_intact_in_skill`

### P1-4: Brittle subprocess test (`test_p14_committed_schema_matches_runtime`)

- **Files changed:** `tests/test_audit_remediation.py`
- **Fix:** The test previously used a relative `PYTHONPATH=src` while changing the child `cwd` to a temporary directory. It now computes an absolute `PYTHONPATH` from the repository root and preserves the existing `PATH`, making it hermetic regardless of editable-install side effects.
- **Regression coverage:** The test continues to assert byte-for-byte schema drift detection.

### P2-1: Repository hygiene — `.DS_Store` and stale artifacts

- **Files changed:** none tracked; removed untracked `.DS_Store` files and stale `dist/venice_media_skill-1.2.1.*` artifacts from the working tree.
- **Verification:** `.gitignore` already blocks `.DS_Store`; `scripts/inspect-sdist.py` already rejects them; `git ls-files` confirms no tracked junk.

### P2-2: Metadata consistency — repository URL casing

- **Files changed:** `pyproject.toml`, `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `THIRD_PARTY_NOTICES.md`, `docs/threat-model.md`, `.github/ISSUE_TEMPLATE/config.yml`
- **Fix:** Normalized all GitHub URLs to the actual repository casing (`spearchucker667/Venice-Media-Skill`).

## 4. Validation Transcript

| Check | Command | Result |
|-------|---------|--------|
| Bytecode compile | `python -m compileall -q src` | PASS |
| Ruff lint | `python -m ruff check .` | PASS |
| Ruff format | `python -m ruff format --check .` | PASS |
| mypy strict | `python -m mypy src` | PASS |
| pytest + coverage | `python -m pytest --cov=venice_media_skill --cov-report=term-missing` | 377 passed, 3 skipped, 82.94% coverage |
| OpenAPI validation | `python -m venice_media_skill validate-openapi references/venice-openapi.yaml` | PASS (`status: ok`) |
| Build wheel + sdist | `python -m build` | PASS |
| sdist inspection | `python scripts/inspect-sdist.py` | PASS (clean, no forbidden entries) |
| Bundled assets mirror check | `python scripts/verify-bundled-assets.py` | PASS |
| pip-audit | `python -m pip_audit` | PASS (no known vulnerabilities) |
| Bandit | `python -m bandit -q -r src` | PASS (warnings only; no test failures) |
| pip check | `python -m pip check` | PASS |
| Full contract | `./scripts/validate.sh` | PASS |
| Wheel install smoke | fresh venv + `dist/*.whl` | PASS (`--help`, `schema`, `validate-openapi`, `importlib.resources`) |
| sdist install smoke | fresh venv + `dist/*.tar.gz` | PASS (`schema`, `validate-openapi`) |

## 5. GitHub Actions Verification

| Run | Event | Result | URL |
|-----|-------|--------|-----|
| `31765342191` | push (`main`) | **PASS** | https://github.com/spearchucker667/Venice-Media-Skill/actions/runs/31765342191 |

All matrix jobs completed successfully:
- Quality / Python 3.11
- Quality / Python 3.12
- Quality / Python 3.13
- Smoke / macos-latest
- Smoke / windows-latest
- Wheel install smoke / Python 3.11
- Wheel install smoke / Python 3.12
- Wheel install smoke / Python 3.13
- Lower-bound deps / Python 3.13
- Dependency and source security

## 6. Residual Risks / Unverified Items

- The July 18 failure logs are no longer retained; the bootstrap-failure root cause is inferred from workflow history and action-version release dates rather than step logs.
- The old manual `workflow_dispatch` run `31764090955` (triggered before the final mypy fix) remains in `queued` state and is superseded by the passing push run.
- Windows PowerShell installer parity was not manually exercised on a Windows host; CI `windows-latest` smoke covers the Python wheel path only.
- DNS rebinding remains mitigated by host allow-listing, not IP pinning (documented in `SKILL.md` and `docs/threat-model.md`).
- No live Venice API calls were made; all tests are offline by design.

## 7. Behavior Changes

- **CI:** Now uses `actions/checkout@v6` and `actions/setup-python@v6`. Dependabot will not auto-propose major-version bumps for these Actions.
- **Content routing:** Host agents are explicitly instructed not to apply native-generator filters to Venice requests, not to invent Venice refusals, and to distinguish host-layer restrictions from Venice provider responses.
- **Keychain module:** Internal refactor to isolate macOS-only logic; no public behavior change.
- **Repository URLs:** Normalized to match the actual GitHub repository casing.

## 8. Documentation-only Changes

- `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `THIRD_PARTY_NOTICES.md`, `docs/threat-model.md`, `.github/ISSUE_TEMPLATE/config.yml`: URL casing only.
- `adapters/generic/AGENT_INSTRUCTIONS.md`: Expanded content-routing guidance.

## 9. Remaining Provider Limitations

- Venice may ignore `hide_watermark` for some content.
- Seedance face-media consent remains an independent legal gate; `safe_mode=false` does not waive it.
- Public downloads are allow-listed and size-capped, but `httpx` re-resolves DNS per connection; IP pinning is not implemented.

---

*End of report.*
