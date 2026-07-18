# Agent workflow

## 1. Detect intent

Trigger when the user explicitly says “using Venice” or requests a supported media task and has enabled this Skill.

Do not switch the host's model provider. The host agent uses shell calls to the bridge.

## 2. Check the environment

```bash
venice-media doctor
```

A missing key should be corrected in the host shell. The user should not paste the key into the model conversation.

## 3. Discover current models

```bash
venice-media models --type image
```

Model IDs, capabilities, privacy mode, beta/deprecation metadata, constraints, and pricing can change. Use the live response rather than examples embedded in documentation.

## 4. Ask model-aware questions

Initial call without a model:

```bash
venice-media plan image.generate --prompt 'A sunset'
```

Second call after model selection:

```bash
venice-media plan image.generate --model '<id>' --prompt 'A sunset'
```

Ask one grouped question. Include defaults so the user can answer “defaults.” Do not ask width/height when a model uses only aspect ratio and resolution. Do not ask steps when the model reports no meaningful steps constraint.

## 5. Write and dry-run a manifest

Use a temporary or project-local `.venice-media/requests` directory. Never include credentials.

Set:

```json
"execution": { "dry_run": true }
```

Run it and inspect the `api_request` object. This validates injected defaults and normalized input fields without consuming credits.

## 6. Quote charged queued jobs

For video and generated music/audio:

```json
"execution": { "quote_first": true }
```

The CLI posts the same canonical payload hash to `/video/quote` or `/audio/quote`. If status is `quote_approval_required` (exit code 6), the runner returns a payload that contains the `quote_response` JSON and the `payload_hash`. Present the quote exactly. After approval, record the approval via:

```bash
echo '<quote_response JSON>' > /tmp/quote.json
venice-media approve-quote <operation> <payload_hash> \
  --quote /tmp/quote.json \
  --max-cost <USD>
```

Then resubmit the unchanged manifest. The bridge attaches the recorded approval to the queue request and verifies it against the same canonical hash. The boolean `confirmed_cost` field is informational only and is ignored by the gate.

## 7. Execute and monitor

The bridge persists the queue ID immediately after a successful queue response. If waiting is enabled, it polls until binary media is available or the local timeout expires.

Do not submit a new generation after timeout. Build a retrieve manifest with the same model and queue ID.

## 8. Handle consent

When Seedance detects face-bearing input, Venice returns `409 needs_consent`. The CLI persists the challenge (with `challenge_id`, `consent_version`, and `policy_text`) and returns `consent_approval_required` (exit code 5). Show the exact `policy_text` and ask the user to confirm all three conditions:

- They accept the returned terms/privacy attestation.
- The likeness is theirs or they have explicit legal permission from every depicted person.
- They acknowledge automated screening.

Only then run:

```bash
venice-media approve-consent <challenge_id> \
  --acknowledge-policy \
  --max-cost <USD>
```

Then resubmit the same media request. Setting `attestations.seedance_face_consent=true` on the manifest alone is not a substitute — the bridge only attaches consents because of an approved challenge tied to the specific payload hash.

## 9. Report exact results

A useful final result includes:

- Operation
- Model ID
- Material parameters
- Queue ID when present
- Artifact paths
- Sidecar paths
- API warnings or provider messages

Never say “generated” from a queue acknowledgement alone.
