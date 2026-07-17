# Agent instructions

## Objective

Maintain a host-neutral, public-ready Venice media bridge. The host agent remains the primary reasoning system; Python performs deterministic API operations.

## Required validation

```bash
./scripts/validate.sh
```

## Invariants

- Output the complete corrected file when modifying a repository artifact.
- Never add secrets or credential storage.
- Preserve live model discovery.
- Preserve image defaults: `safe_mode=false`, `hide_watermark=true`.
- Preserve explicit Seedance legal-consent confirmation.
- Preserve quote gating and timeout-safe queue recovery.
- Do not silently retry paid queue submissions.
- Do not forward authorization to external download URLs.
- Update tests and documentation with implementation changes.
- Keep references provenance intact.
