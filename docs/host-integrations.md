# Host integrations

## Kimi Code

Install the Skill under either:

```text
~/.kimi-code/skills/venice-media/SKILL.md
~/.agents/skills/venice-media/SKILL.md
```

Invoke:

```text
/skill:venice-media using Venice, create an image of a sunset
```

The directory form is recommended because it permits co-located references.

## Generic Agent Skills hosts

Copy `skills/venice-media/` into the host's user-level or project-level Agent Skills directory. The host requires shell execution permission for the `venice-media` executable.

## Codex, Claude Code, Gemini CLI, and OpenCode

Tool-specific skill discovery changes over time. The stable integration is:

1. Install the Python bridge globally for the user.
2. Install the host-neutral Skill under `.agents/skills/venice-media/` when the host supports Agent Skills.
3. Otherwise include `adapters/generic/AGENT_INSTRUCTIONS.md` and the full Skill body in the host's persistent project instructions.
4. Permit only the `venice-media` command and ordinary file writes to the request/output directories.

Do not add the Venice API key to project instruction files, MCP configuration, command aliases, or manifests.

## Project-local installation

For a repository-scoped skill:

```text
<project>/.agents/skills/venice-media/SKILL.md
```

This is useful when generation conventions, output paths, or review rules are project-specific. Keep the Python bridge user-installed so the repository does not commit a virtual environment.

## Non-interactive hosts

The CLI returns JSON suitable for orchestration:

```bash
venice-media --compact run request.json
```

Exit codes:

| Code | Meaning |
|---:|---|
| 0 | Command completed and emitted a normal status (e.g. `completed`, `queued`, `timed_out`). |
| 2 | Local configuration, validation, or filesystem failure. |
| 3 | Venice API error. |
| 4 | Venice returned a raw `409 needs_consent` condition that still requires explicit handling. Surface the provider policy and do not retry automatically. |
| 5 | A persisted Seedance challenge awaits explicit approval, or Venice accepted a paid queue but its durable local record could not be written. Follow the structured `next_step`; use `approve-consent` for a challenge and retrieve by the returned queue ID after a durable-write failure. |
| 6 | Quote approval required (paid queued operation). Run `venice-media approve-quote <operation> <payload_hash> --quote <file> --max-cost <USD>` after the user confirms the price. |
| 7 | Network-safety violation (e.g. absolute URL, scheme-relative path, non-HTTPS, private IP). |
| 8 | Quote approval hash no longer matches the queued payload. Resubmit a fresh quote. |
| 9 | Transport error (DNS, TCP, TLS). |

A non-interactive wrapper must preserve stderr and exit code, then parse stdout only when exit code is zero.
