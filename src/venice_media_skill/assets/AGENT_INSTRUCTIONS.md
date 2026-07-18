# Generic AI CLI integration

Keep the host model as the primary agent. Install `skills/venice-media/` into the host's Agent Skills directory when supported. Otherwise, add the complete `SKILL.md` body to the host's persistent instruction system and allow shell execution of the `venice-media` command.

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

For `image.generate`, preserve the exact refreshed image-model ID and use `parameters.variants` as the image count. Omitted/`1` selects one binary image and the bridge omits the wire-level `variants` field; `2`–`4` selects JSON mode. Never add `return_binary` to a manifest. Dry-run before generation and stop after one repeated identical provider failure.
