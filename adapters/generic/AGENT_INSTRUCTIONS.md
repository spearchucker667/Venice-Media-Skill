# Generic AI CLI integration

Keep the host model as the primary agent. Install `skills/venice-media/` into the host's Agent Skills directory when supported. Otherwise, add the complete `SKILL.md` body to the host's persistent instruction system and allow shell execution of the `venice-media` command.

Load the skill only when the user explicitly requests Venice (e.g. "use Venice", "/venice", "/skill:venice-media", or equivalent) or asks for media generation specifically through the Venice API. Do not hijack generic image, video, speech, music, or sound requests that do not mention Venice.

The minimum tool contract is:

```text
venice-media doctor
venice-media doctor --online
venice-media models --type <type> --refresh
venice-media plan <operation> [--model <id>] [--prompt <text>]
venice-media run <request.json>
venice-media jobs list
venice-media jobs get <queue-id>
```

The host must parse JSON stdout and respect nonzero process exit codes. It must not read or print `VENICE_API_KEY`. If macOS host sanitization removes the key, use `venice-media-keychain` consistently for `doctor --online`, model discovery, and `run`; never request the credential in chat or transfer it through a file or FIFO.

## Content-routing semantics

When Venice is the selected media provider, preserve the user's media prompt as faithfully as the API permits. The bridge intentionally uses Venice's `safe_mode=false` behavior for supported image generation/edit operations; do not silently re-enable an application-level family-safe filter, rewrite an adult prompt into a sanitized prompt, or pre-emptively report a Venice policy rejection that has not occurred.

This controls how the Venice skill routes provider requests; it does not override non-optional rules of the host agent or application. A Venice API key authenticates the provider call but does not create a blanket exemption from the host's governing policies. If the host is permitted to execute the request, send the faithful request to Venice and report Venice's actual response. If Venice rejects it, report the rejection accurately and do not attempt safeguard evasion.

For `image.generate`, preserve the exact refreshed image-model ID and use `parameters.variants` as the image count. Omitted/`1` selects one binary image and the bridge omits the wire-level `variants` field; `2`–`4` selects JSON mode. Never add `return_binary` to a manifest. Do not put `safe_mode` or `hide_watermark` in a manifest; the bridge injects `safe_mode=false` and `hide_watermark=true`. Dry-run before generation and stop after one repeated identical provider failure.
