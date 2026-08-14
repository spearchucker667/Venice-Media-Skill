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
| Linux | VERIFIED | n/a | VERIFIED | NOT TESTED | Published-release run `31849635295` verified the downloaded v1.3.1 wheel, sdist, checksums, bundled resources, and generic project installation on Ubuntu. |
| Windows | VERIFIED | n/a | VERIFIED | NOT TESTED | CI run `31849875746` verified the hardened native installer and uninstall safety contract. Published-release run `31849635295` verified the v1.3.1 wheel/sdist smoke, then FAILED at the expected unsafe native-installer case; that defect is fixed for v1.3.2. |

No charged Venice generation was executed. Live API status means authenticated connectivity and model discovery only.
