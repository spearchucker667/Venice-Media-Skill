# Generic AI CLI integration

Keep the host model as the primary agent. Install `skills/venice-media/` into the host's Agent Skills directory when supported. Otherwise, add the complete `SKILL.md` body to the host's persistent instruction system and allow shell execution of the `venice-media` command.

The minimum tool contract is:

```text
venice-media doctor
venice-media models --type <type>
venice-media plan <operation> [--model <id>] [--prompt <text>]
venice-media run <request.json>
venice-media jobs list
venice-media jobs get <queue-id>
```

The host must parse JSON stdout and respect nonzero process exit codes. It must not read or print `VENICE_API_KEY`.
