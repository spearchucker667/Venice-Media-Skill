# Compatibility matrix

Statuses are evidence labels, not documentation claims:

- **VERIFIED** — exercised with the named runtime or platform.
- **PARTIAL** — a meaningful subset passed, with the unexecuted boundary stated.
- **NOT TESTED** — the required runtime or authorization was unavailable.
- **FAILED** — an exercised acceptance path did not work.

Evidence was collected on 2026-08-14. See [v1.3.1 external acceptance](audits/v1.3.1-external-acceptance.md) for commands and release-artifact hashes.

| Host / environment | Install | Skill discovery | CLI invocation | Live API | Notes |
|---|---|---|---|---|---|
| Codex CLI 0.148.0-alpha.9 | VERIFIED | VERIFIED | VERIFIED | PARTIAL | A fresh ephemeral Codex context discovered `.agents/skills/venice-media/SKILL.md`. Offline CLI commands passed after the sandbox lock-directory defect was fixed; live catalog checks were exercised directly through the Keychain launcher, not from the disposable Codex session. |
| Claude Code 2.1.197 | VERIFIED | NOT TESTED | NOT TESTED | NOT TESTED | Generic project layout was installed from the published wheel. Runtime acceptance could not start because the configured Claude account reported `Credit balance is too low`. |
| Gemini CLI 0.47.0 | VERIFIED | VERIFIED | NOT TESTED | NOT TESTED | `gemini skills list` discovered and enabled the project skill. Headless invocation required an unavailable interactive authentication flow; admin policy also disabled YOLO mode. |
| OpenCode 1.18.15 | VERIFIED | VERIFIED | VERIFIED | NOT TESTED | `opencode run --auto` loaded the project skill and ran offline `--version` and `doctor` commands successfully. |
| Kimi Code CLI 0.36.0 (`kimi`) | VERIFIED | VERIFIED | VERIFIED | NOT TESTED | A prompt-mode session loaded `.kimi-code/skills/venice-media/SKILL.md` and ran offline `--version` and `doctor` commands successfully. |
| macOS 27.0 arm64 | VERIFIED | n/a | VERIFIED | VERIFIED | Downloaded wheel/sdist, generic/Kimi installs, host runtimes, Keychain auth, model discovery, planning, and 11-operation dry-run matrix exercised. |
| Linux | PARTIAL | n/a | PARTIAL | NOT TESTED | Checkout-built Linux CI was green at baseline. Published-release Linux smoke is added for the v1.3.2 release cycle. |
| Windows | PARTIAL | n/a | PARTIAL | NOT TESTED | Existing checkout-built smoke was green. Native PowerShell installer regression coverage and published-release Windows smoke are added for hosted execution before v1.3.2 publication. |

No charged Venice generation was executed. Live API status means authenticated connectivity and model discovery only.
