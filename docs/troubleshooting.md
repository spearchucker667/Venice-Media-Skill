# Troubleshooting

## `venice-media: command not found`

Ensure `~/.local/bin` is on `PATH`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Start a new host-agent session after installation.

## Missing API key

```bash
export VENICE_API_KEY='...'
venice-media doctor --online
```

Do not add the key to a request JSON file.

## Model not found

Refresh live models:

```bash
venice-media models --type all --refresh
```

The model may have been removed, deprecated, region-restricted, or unavailable to the account.

## Unsupported parameter

Run a model-specific plan and compare its constraints:

```bash
venice-media plan image.generate --model '<id>' --prompt '...'
```

Remove fields the selected model does not advertise. Some native endpoint fields are accepted but ignored by specific models; API warnings are authoritative.

## Image returns JSON instead of a file

Multiple variants require non-binary image output. The bridge decodes base64 image objects. Preserve the raw error payload if decoding fails and open an issue with a sanitized response shape.

## Video or audio timed out

Do not submit another generation. Retrieve the existing queue:

```json
{
  "operation": "video.retrieve",
  "model": "MODEL_ID",
  "parameters": { "queue_id": "QUEUE_ID" }
}
```

## `consent_required`

Show the returned `policy_text`. After explicit confirmation, set `attestations.seedance_face_consent=true` and resubmit the same request. Do not send `consent_version`.

## HTTP 400

Typical causes:

- Missing required model-specific field
- Invalid aspect ratio/resolution/duration combination
- Wrong voice for a TTS model
- Malformed local media data
- Extra or malformed Seedance consent fields

Use dry-run and inspect the redacted API request.

## HTTP 402

The account or x402 balance is insufficient. This package currently uses API-key authentication; fund or manage the Venice account outside the bridge.

## HTTP 422 provider policy

Report the provider message and any recommended model. Do not rewrite the request into a claimed success. Credits/refund status should be stated only when present in the response.

## OpenAPI validation

```bash
venice-media validate-openapi references/venice-openapi.yaml
```

This checks YAML parsing and required media paths, not semantic compatibility with every live model.
