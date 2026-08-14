# Venice Media Skill documentation

## Guides

- [Architecture](architecture.md) — system boundary, modules, trust zones, failure model
- [Agent workflow](agent-workflow.md) — how host agents interact with the bridge
- [Media generation guide](media-generation-guide.md) — per-operation guidance and input contracts
- [Host integrations](host-integrations.md) — setup for Kimi Code, Claude Code, Codex, Gemini CLI, OpenCode
- [API synchronization](api-sync.md) — how to refresh the pinned OpenAPI snapshot
- [Releasing](releasing.md) — release process and checklist
- [Troubleshooting](troubleshooting.md) — common issues and fixes

## Security and governance

- [Security & privacy](security-and-privacy.md) — invariants and best practices
- [Threat model](threat-model.md) — comprehensive security analysis
- [Audits](audits/) — historical audit remediation records

## Reference

- [API snapshot provenance](api-reference-snapshot.md) — upstream source and version for `references/venice-openapi.yaml`
- [Request JSON Schema](../references/request.schema.json) — machine-readable manifest schema
- [Venice OpenAPI snapshot](../references/venice-openapi.yaml) — reviewed provider contract
- [Changelog](../CHANGELOG.md)
