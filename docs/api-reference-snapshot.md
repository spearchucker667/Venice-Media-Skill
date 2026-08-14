# API reference snapshot

The repository includes a reviewed Venice OpenAPI snapshot at `references/venice-openapi.yaml`.

## Upstream provenance

| Field | Value |
|---|---|
| Upstream repository | `https://github.com/veniceai/api-docs` |
| Upstream commit | `db3b9f4f40fe71abff2011bcaa9c23ad797c94f3` |
| Retrieved | `2026-08-14` |
| OpenAPI `info.version` | `20260814.153445` |
| OpenAPI spec version | `3.0.0` |
| Server base | `https://api.venice.ai/api/v1` |

The snapshot documents the request and response contracts used by the bridge. Live model capabilities and pricing are still obtained from `GET /models` because model availability changes more frequently than package releases.

## Refresh procedure

Use `scripts/sync-venice-api-docs.py` to refresh from a local upstream checkout. See [docs/api-sync.md](api-sync.md) for exact commands.

## Required paths

CI validates required media paths in the snapshot, including models, native image generation/editing/upscale/background removal, TTS/transcription, voice cloning, video transcription, audio queue/retrieve/quote, and video queue/retrieve/quote.
