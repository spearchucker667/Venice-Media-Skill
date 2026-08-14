# Venice Media Skill

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Code Style: Ruff](https://img.shields.io/badge/code%20style-ruff-ff69b4.svg)](https://github.com/astral-sh/ruff)
[![Type Checked: mypy](https://img.shields.io/badge/type%20checked-mypy-3078C6.svg)](https://mypy-lang.org/)
[![Tests: pytest](https://img.shields.io/badge/tests-pytest-0A9EDC.svg)](https://docs.pytest.org/)

**Venice Media Skill** is a host-neutral Agent Skill and Python bridge that lets an existing AI CLI use the Venice API for media generation **without replacing the original host agent**.

The host agent—Kimi Code, Claude Code, Codex, Gemini CLI, OpenCode, or another shell-capable interface—continues to reason, ask questions, and manage the conversation. This package provides a narrow subprocess boundary for:

- 🎨 **Images**: generate, edit, multi-edit, upscale, background removal
- 🎬 **Video**: generate, retrieve, transcribe; reference/edit/extend/stitch workflows expressed through `video.generate` inputs and prompt tokens
- 🔊 **Audio**: text-to-speech (TTS), music/audio generation, retrieve, transcribe, voice cloning
- 🔍 **Live model discovery** and model-aware parameter planning
- ✅ **Quotes, queue persistence, polling, artifact storage, and redacted metadata sidecars**

All CLI stdout is JSON; diagnostics and errors go to stderr.

---

## Quick start

### Install

```bash
# Clone and install in editable mode for development
git clone https://github.com/spearchucker667/Venice-Media-Skill.git
cd Venice-Media-Skill
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

# Or use the install script
./scripts/install.sh --host generic --scope user
```

### Authenticate

Set the API key in your shell. Never commit it.

```bash
export VENICE_API_KEY='your-venice-api-key-here'
```

On macOS, store the key in Keychain under service `venice-api-key` and account `$USER`, then use `venice-media-keychain` so agent subprocesses can resolve it.

### Verify

```bash
venice-media --version
venice-media doctor
venice-media doctor --online
```

### Generate an image

```bash
venice-media plan image.generate --prompt 'A cinematic sunset over a quiet ocean'
venice-media run examples/requests/image-generate.json
```

A dry-run manifest prints the resolved provider payload without spending credits:

```json
{
  "version": "1",
  "operation": "image.generate",
  "model": "MODEL_FROM_LIVE_CATALOG",
  "prompt": "A cinematic sunset over a quiet ocean",
  "parameters": { "aspect_ratio": "1:1", "resolution": "1K", "variants": 1 },
  "execution": { "dry_run": true }
}
```

---

## Supported operations

| Media | Operations |
|---|---|
| Images | `image.generate`, `image.edit`, `image.multi_edit`, `image.upscale`, `image.background_remove` |
| Video | `video.generate`, `video.retrieve`, `video.transcribe` |
| Audio | `audio.generate`, `audio.retrieve`, `audio.tts`, `audio.transcribe`, `audio.voice_clone` |

---

## Documentation

- [Architecture & trust zones](docs/architecture.md)
- [Agent workflow](docs/agent-workflow.md)
- [Media generation guide](docs/media-generation-guide.md)
- [API sync procedure](docs/api-sync.md)
- [Host integrations](docs/host-integrations.md)
- [Security & privacy](docs/security-and-privacy.md)
- [Threat model](docs/threat-model.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Releasing](docs/releasing.md)
- [Changelog](CHANGELOG.md)

Reference materials:

- [Request JSON Schema](references/request.schema.json)
- [Venice OpenAPI snapshot](references/venice-openapi.yaml)

---

## Security invariants

- API keys are **never stored** by the bridge; `VENICE_API_KEY` is read from the environment or macOS Keychain only.
- Downloads use an isolated, unauthenticated client with HTTPS, allow-list, and SSRF protections.
- Paid queued operations require explicit quote approval; queue timeouts never auto-resubmit.
- Seedance face-media consent requires explicit user approval of a persisted challenge.
- Reserved provider/transport keys cannot be injected through `parameters`.

See [docs/security-and-privacy.md](docs/security-and-privacy.md) for the full policy.

---

## Development

```bash
python -m pip install -e '.[dev]'
./scripts/validate.sh
```

---

## License

[MIT](LICENSE)
