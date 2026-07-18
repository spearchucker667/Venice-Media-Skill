# Troubleshooting

## `venice-media: command not found`

Ensure `~/.local/bin` is on `PATH`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Start a new host-agent session after installation.

## Missing API key

First run `venice-media --version`, `venice-media doctor`, and `venice-media doctor --online`. The online check is authoritative: credentials are opaque, and no key prefix proves validity or invalidity.

If the key works in Terminal but is missing in an agent subprocess, the host has likely sanitized its environment. On macOS, store the item under service `venice-api-key` and account `$USER`, then use the installed launcher:

```bash
venice-media-keychain doctor --online
```

Use Keychain Access to create or update the generic-password item so the credential never appears in a command argument. Optional non-secret overrides are:

```bash
VENICE_KEYCHAIN_ACCOUNT='account-name' \
VENICE_KEYCHAIN_SERVICE='service-name' \
venice-media-keychain doctor --online
```

Set `VENICE_MEDIA_EXECUTABLE=/custom/path/venice-media` only to select a known installation. Otherwise the launcher prefers a sibling executable and then `PATH`. It rejects a target that resolves back to itself. Re-run `./scripts/install.sh ...` to upgrade both launchers; `./scripts/uninstall.sh --bridge` removes both.

Do not paste or resend credentials in chat, pass them as command-line arguments, use FIFOs or temporary secret files, or put them in manifests. Plaintext `.env` files are not the default. Rotate any credential exposed in chat, logs, screenshots, issue reports, or shell history.

## Multiple executables

Run `venice-media installations` (or `command -v -a venice-media` in zsh) to report every PATH candidate, the active executable, its resolved target, Keychain launcher target, Python interpreter, package version/location, editable status, and missing runtime dependencies. This command is read-only. A global executable importing an editable checkout through the wrong interpreter is stale and should be reinstalled in a self-contained environment.

## Model not found

Refresh live models:

```bash
venice-media models --refresh
```

The model may have been removed, deprecated, region-restricted, or unavailable to the account.

## Unsupported parameter

Run a model-specific plan and compare its constraints:

```bash
venice-media plan image.generate --model '<id>' --prompt '...'
```

Remove fields the selected model does not advertise. Some native endpoint fields are accepted but ignored by specific models; API warnings are authoritative.

## Image returns JSON instead of a file

Multiple variants require JSON image output. For one image, the bridge sends `return_binary=true` and omits `variants`; for 2–4 images it sends `return_binary=false` with the requested count. The bridge decodes the native raw-base64 `images` array, validates every variant before publishing any file, and writes ordered unique artifacts. Preserve the sanitized response shape if decoding fails; do not retry the same charged request repeatedly.

If Venice reports that `variants` is unsupported with binary mode, run a dry-run and verify that its `output_plan` says `image_count: 1`, `response_mode: binary`, and `variants_field: omitted`. Reinstall the bridge if the dry-run still shows `variants: 1` in `api_request`.

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

Show the returned `policy_text` and the `challenge_id` from the CLI output. After the user explicitly confirms every depicted likeness and the policy text, run:

```bash
venice-media approve-consent <challenge_id> \
  --acknowledge-policy \
  --max-cost <USD>
```

Then resubmit the same request. Setting `attestations.seedance_face_consent=true` on the manifest alone is informational only; the bridge only attaches consents because of a stored, hash-bound approval tied to that specific payload. Never add `consent_version` to the manifest — Venice tracks it server-side.

## HTTP 400

Typical causes:

- Missing required model-specific field
- Invalid aspect ratio/resolution/duration combination
- Wrong voice for a TTS model
- Malformed local media data
- Extra or malformed Seedance consent fields

Use dry-run and inspect the redacted API request. For a provider message claiming that a readable local image is incomplete or corrupt, do not immediately re-encode and retry: verify file readability, detected MIME and dimensions, the endpoint's required wire encoding, and endpoint constraints in that order. `image.upscale` requires raw base64, while edit and queued-video media retain data-URL or URL encoding. Re-encode only with evidence of an incompatible file structure. Never perform repeated charged retries after an identical provider failure.

## HTTP 402

The account or x402 balance is insufficient. This package currently uses API-key authentication; fund or manage the Venice account outside the bridge.

## HTTP 422 provider policy

Report the provider message and any recommended model. Do not rewrite the request into a claimed success. Credits/refund status should be stated only when present in the response.

## OpenAPI validation

```bash
venice-media validate-openapi references/venice-openapi.yaml
```

This checks YAML parsing and required media paths, not semantic compatibility with every live model.
