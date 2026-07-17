# API reference snapshot

The repository includes a reviewed Venice OpenAPI snapshot at `references/venice-openapi.yaml`.

Provenance recorded in the file:

- Source: `https://api.venice.ai/doc/api/swagger.yaml`
- Retrieved: `2026-07-11`
- Content version: `20260709.204640`
- OpenAPI: `3.0.0`
- Server: `https://api.venice.ai/api/v1`

The snapshot documents the request and response contracts used by the bridge. Live model capabilities and pricing are still obtained from `GET /models` because model availability changes more frequently than package releases.

Required paths validated by CI include models, native image generation/editing/upscale/background removal, TTS/transcription, audio queue/retrieve/quote, and video queue/retrieve/quote.
