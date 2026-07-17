# Architecture

## Boundary

```text
User
  ↓
Existing host agent (Kimi, Codex, Claude Code, Gemini CLI, OpenCode, ...)
  ├─ reasons and asks clarifying questions
  ├─ calls `venice-media plan`
  ├─ writes a versioned request manifest
  └─ calls `venice-media run`
        ↓
Python bridge
  ├─ validates the manifest
  ├─ loads VENICE_API_KEY from the environment
  ├─ queries live model metadata
  ├─ normalizes explicit local media inputs
  ├─ calls Venice REST endpoints
  ├─ persists queue state
  ├─ polls or resumes retrieval
  └─ writes artifacts and redacted metadata
        ↓
Venice API
```

The host agent remains the conversational and reasoning authority. The bridge has no LLM loop and does not call Venice chat completions.

## Modules

| Module | Responsibility |
|---|---|
| `config.py` | Environment, platform directories, credential rejection in config files. |
| `client.py` | Bearer-authenticated HTTP, binary responses, structured errors, consent detection. |
| `catalog.py` | Live `GET /models` discovery and one-hour cache. |
| `planner.py` | Converts model constraints into grouped host-agent questions. |
| `request.py` | Versioned manifest parsing, validation, and JSON Schema. |
| `runner.py` | Operation dispatch, quote gates, queue polling, consent payload construction. |
| `jobs.py` | Durable queue records and recovery metadata. |
| `output.py` | Binary/base64 decoding, collision-safe filenames, sidecar metadata. |
| `cli.py` | Stable JSON command contract and exit codes. |

## Trust zones

1. **Host context:** User intent and conversation. It should not contain API secrets.
2. **Local bridge process:** Receives an explicit manifest and environment key.
3. **Local filesystem:** Stores selected inputs, artifacts, sidecars, cache, and queue records.
4. **Venice API:** Receives prompts, parameters, and explicitly selected media.
5. **Pre-signed download host:** Receives only the pre-signed URL request. The bridge uses a separate unauthenticated HTTP client so the Venice Bearer token is not forwarded.

## State

The CLI uses `platformdirs`:

- Config: user config directory; optional non-secret `config.json`
- Cache: live model response cache
- State: queue records
- Output: current-directory `venice-media-output` unless overridden

Queue records contain a redacted request, request hash, model, queue ID, timestamps, status, and artifact paths. They never contain the API key.

## Failure model

- Validation failures occur before API calls.
- API failures retain HTTP status, request ID, and structured payload where available.
- `409 needs_consent` is a dedicated result, not a generic retry.
- Poll timeout is not generation failure. The queue ID remains resumable.
- A completed status with a pre-signed URL is downloaded without authorization forwarding.
- Artifact write failure does not cause automatic generation resubmission.
