---
name: venice-media
description: Use the Venice API from the current AI CLI to generate or edit images, create or retrieve videos, synthesize TTS, generate music/audio, upscale/remove backgrounds, and transcribe audio without replacing the current host agent.
type: prompt
whenToUse: When the user says to use Venice or asks for image, video, speech, music, sound, image editing, upscaling, background removal, or transcription through the Venice API.
disableModelInvocation: false
arguments:
  - request
---

# Venice Media Skill

You are still the user's original host agent. Do **not** replace your reasoning model, current session, or provider with a Venice chat model. Use the local `venice-media` Python CLI as a subprocess only for Venice model discovery and media API execution.

The user's request is:

`$request`

When `$request` is empty, use the current user message.

## Non-negotiable behavior

1. Never expose, print, store, echo, or place `VENICE_API_KEY` in a request manifest, log, command argument, source file, shell history entry, or assistant response.
2. Never invent model capabilities, model IDs, prices, supported sizes, durations, voices, or constraints. Query the live model catalog.
3. Image generation defaults are `safe_mode=false` and `hide_watermark=true`. Do not ask the user about these defaults unless they ask to change them. Venice may still ignore watermark settings for some content.
4. Do not bypass provider or platform policy failures. Report API errors accurately.
5. Never assert Seedance face-media consent on the user's behalf. The bridge stores a `consent_required` challenge (with a `challenge_id`) and only attaches the consents body to the queue request after the user runs `venice-media approve-consent <challenge_id> --acknowledge-policy --max-cost <USD>`. The boolean `attestations.seedance_face_consent` flag on the manifest is informational only — the CLI does not accept a value of `true` as consent.
6. For video and queued audio/music, request a quote first unless the user explicitly supplied an approved budget or explicitly instructed immediate generation with known cost.
7. Treat local media as sensitive. Do not upload unrelated files. Resolve explicit paths only.
8. Keep stdout machine-readable by using the CLI's JSON output. Parse the result; do not guess success from process exit alone.

### Known boundaries of the current protection

- **`safe_mode=false` is not a waiver of Seedance consent.** Image-generation defaults set `safe_mode=false` for convenience. A Seedance `409 needs_consent` is an *independent* legal gate: surface the exact policy, obtain explicit confirmation, then attach the consent. The bridge never auto-resubmits and treats a `consent_required` outcome as terminal until approval lands.
- **DNS rebinding is mitigated by the host allow-list, not by IP pinning.** The bridge validates the resolved IP against the operator allow-list before each request, but `httpx` re-resolves on connect. Until custom-transport IP pinning lands, do not assume a returned-media URL is "safe" beyond what the allow-list enumerates.
- **Authenticated calls honor the process proxy** (`HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY`); **public downloads do not** (`trust_env=False`). Trade-off the trade-off consciously — public-download safety is enforced by an explicit allow-list; authenticated calls are subject to whatever proxy the host user-agent configured.

## Environment check

Run:

```bash
command -v venice-media >/dev/null 2>&1 && venice-media doctor
```

If the command is missing, stop and tell the user to install this repository. If `VENICE_API_KEY` is missing, ask them to export it in their shell. Do not ask them to paste the key into chat.

## Bundled API reference

The installed Skill includes a pinned Venice OpenAPI snapshot at `references/venice-openapi.yaml` and a capability index at `references/venice-api-llms.md`. Use them to verify endpoint fields and response shapes. Treat the live `GET /models` response as authoritative for the currently available model IDs, pricing, traits, and model-specific constraints. Never load the entire Swagger file into context when a targeted search for the relevant endpoint or schema is sufficient.

## Plan before executing

Map the request to one operation:

- `image.generate`
- `image.edit`
- `image.multi_edit`
- `image.upscale`
- `image.background_remove`
- `video.generate`
- `video.retrieve`
- `audio.tts`
- `audio.generate`
- `audio.retrieve`
- `audio.transcribe`

Call:

```bash
venice-media plan <operation> --prompt '<user prompt>'
```

If a model is already known:

```bash
venice-media plan <operation> --model '<model-id>' --prompt '<user prompt>'
```

Read the returned `questions`. Ask one grouped clarification message containing only required or materially useful model-supported fields. For image generation, this commonly includes model, dimensions or aspect ratio, resolution, CFG, negative prompt, steps, seed, variants, and format—but only ask fields supported or meaningfully accepted by the selected model. Offer the returned defaults inline.

Do not repeatedly interrogate the user. When they say “use defaults,” use planner defaults plus model defaults.

## Build a request manifest

Create a temporary JSON file under `.venice-media/requests/` in the current project, or the OS temporary directory when the current directory should not be modified. Use request schema version `1`.

Minimal image example:

```json
{
  "version": "1",
  "operation": "image.generate",
  "model": "MODEL_FROM_LIVE_CATALOG",
  "prompt": "A cinematic sunset over a quiet ocean",
  "parameters": {
    "aspect_ratio": "1:1",
    "resolution": "1K",
    "negative_prompt": "",
    "variants": 1,
    "format": "webp"
  },
  "output": {
    "directory": "./venice-media-output",
    "write_metadata": true
  },
  "execution": {
    "dry_run": false
  }
}
```

Do not put `safe_mode`, `hide_watermark`, or `return_binary` in the manifest. These are bridge-controlled fields: the bridge injects `safe_mode=false` and `hide_watermark=true` for image generation, selects binary return behavior from `variants`, and injects `safe_mode=false` for image editing.

Before a charged queued request, set:

```json
"execution": {
  "quote_first": true,
  "wait": true
}
```

Run the manifest. The bridge posts the same canonical payload hash to `/video/quote` or `/audio/quote`. If status is `quote_approval_required` (exit code 6), the runner returns a `quote_approval_required` payload that includes the `payload_hash` and the `quote_response` JSON. Show the quote to the user. After they explicitly approve the cost, record the approval via:

```bash
echo '<quote_response JSON>' > /tmp/quote.json
venice-media approve-quote <operation> <payload_hash> \
  --quote /tmp/quote.json \
  --max-cost <USD>
```

Then resubmit the same manifest. `confirmed_cost` is informational and not consulted by the gate. The bridge records the approval, attaches it to the queue request, and posts the queue body with the same canonical hash so the actor cannot accidentally quote a different request than the one queued.

## Execute

First validate without spending credits when useful:

```bash
venice-media run request.json
```

A request with `execution.dry_run=true` returns the endpoint and redacted API payload without calling Venice. Change it to `false` only after the manifest is correct.

For real execution:

```bash
venice-media run request.json
```

Interpret outcomes:

- `completed`: Return each artifact path and metadata sidecar path.
- `queued` or `processing`: Return the queue ID and preserve it. Use a retrieve manifest later.
- `quote_approval_required`: Show the `quote_response` returned with the gate output. Wait for explicit user approval, then run `venice-media approve-quote <operation> <payload_hash> --quote <file> --max-cost <USD>` and resubmit the unchanged manifest.
- `consent_approval_required`: Show the exact Venice `policy_text` and `consent_version` carried in the `consent_required` payload. Wait for explicit confirmation, then run `venice-media approve-consent <challenge_id> --acknowledge-policy --max-cost <USD>` and resubmit the same media request. Setting `attestations.seedance_face_consent=true` on the manifest alone is not a substitute.
- `timed_out`: Do not queue a duplicate. Use the queue ID with `video.retrieve` or `audio.retrieve`.
- `error`: Report status code, request ID, and provider message. Do not claim credits were or were not charged unless the response states it.

## Seedance 2.0 workflow rules

For `seedance-2-0-reference-to-video` and its fast variant, preserve canonical case-sensitive reference tokens exactly:

- `<Image 1>`
- `<Video 1>`
- `<Audio 1>`

Use the workflow-specific prompt shapes in `references/seedance-2-0-api-guide.md`:

- Reference: `Refer to ... in <Image|Video|Audio N> to generate ...`
- Edit: `Strictly edit <Video 1>, changing ...`
- Extend: `Extend <Video 1>, generate ...`
- Stitch: `<Video 1> + transition + followed by <Video 2>`

Do not invent a separate workflow field; Seedance infers the workflow from prompt shape and supplied media.

## Final response

Report:

- Operation and model
- Relevant settings actually used
- Quote and approval state when applicable
- Queue ID for queued work
- Absolute artifact path(s)
- Metadata sidecar path(s)
- Any API warning, ignored parameter, provider rejection, or consent requirement

Do not claim that a file was generated unless the CLI returned `status: completed` and an artifact path.
