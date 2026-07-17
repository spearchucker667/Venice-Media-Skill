# Venice Media Skill

A public-ready, host-neutral Agent Skill and Python bridge that lets an existing AI CLI use the Venice API for media generation **without replacing the original host agent**.

The host agent—Kimi Code, Codex, Claude Code, Gemini CLI, OpenCode, or another shell-capable interface—continues to reason, ask questions, and manage the conversation. This package provides a narrow subprocess boundary for:

- Image generation
- Image editing and multi-edit
- Image upscaling
- Background removal
- Video generation, retrieval, editing, extension, and stitching through supported Venice models
- Text-to-speech
- Music and generated audio
- Audio transcription
- Live model discovery and model-aware parameter planning
- Quotes, queue persistence, polling, artifact storage, and metadata sidecars

## Design goals

- **Preserve the original agent.** Venice is used as a media API, not as a replacement chat model.
- **Discover capabilities live.** The bridge queries `GET /models`; it does not rely on a stale hard-coded model matrix.
- **Agent-readable I/O.** Commands emit structured JSON to stdout and errors to stderr.
- **Safe credential boundary.** `VENICE_API_KEY` is read only from the process environment and is never written to manifests or config files.
- **Recover queued jobs.** Video and generated-audio queue IDs are stored locally for later retrieval.
- **Prevent duplicate spend.** Timeouts return a resumable queue ID instead of automatically submitting another generation.
- **Quote before queued generation.** Video and generated audio can return a quote and require explicit approval before queueing.
- **Model-aware clarification.** The host asks only questions relevant to the selected model's current constraints.
- **Auditable outputs.** Every media artifact can receive a JSON metadata sidecar with the model, prompt, parameters, queue ID, and redacted request.

## Defaults

For native Venice image generation, the bridge injects:

```json
{
  "safe_mode": false,
  "hide_watermark": true
}
```

For image edit and multi-edit, it injects `safe_mode=false`. Venice may ignore watermark settings for certain content. These settings do not override provider-level or platform-level request rejection.

Seedance face-media consent is never inferred or auto-approved. When Venice returns `409 needs_consent`, the CLI emits a structured `consent_required` result containing the exact policy text. The host must show that text and obtain an explicit legal attestation before resubmitting.

## Repository layout

```text
venice-media-skill/
├── skills/venice-media/          # Host-neutral Agent Skill
├── adapters/
│   ├── kimi-code/                # Native Kimi Code skill/plugin package
│   └── generic/                  # Persistent-instruction fallback
├── src/venice_media_skill/       # Python bridge
├── references/
│   ├── venice-openapi.yaml       # Bundled Venice Swagger/OpenAPI snapshot
│   ├── venice-api-llms.md        # Venice documentation index snapshot
│   ├── seedance-2-0-api-guide.md
│   ├── seedance-face-consent-api-guide.md
│   └── request.schema.json
├── examples/requests/            # Complete request manifests
├── docs/                         # Architecture, security, integrations, workflows
├── tests/                        # Unit and CLI contract tests
├── scripts/                      # Cross-platform installers and validation
└── .github/                      # CI, releases, issue templates, Dependabot
```

## Requirements

- Python 3.11 or newer
- A Venice API key
- A host agent able to execute shell commands
- macOS, Linux, WSL, or Windows PowerShell

## Install

### macOS / Linux / WSL

```bash
git clone https://github.com/spearchucker667/venice-media-skill.git
cd venice-media-skill
./scripts/install.sh --host kimi --scope user
```

The installer creates an isolated virtual environment at:

```text
~/.local/share/venice-media-skill/venv
```

It installs a launcher at `~/.local/bin/venice-media` and copies the complete Skill—including the request schema, Venice capability index, full pinned Swagger/OpenAPI snapshot, and Seedance workflow references—to:

```text
~/.agents/skills/venice-media/
~/.kimi-code/skills/venice-media/   # when --host kimi is selected
```

Ensure `~/.local/bin` is on `PATH`.

### Windows PowerShell

```powershell
git clone https://github.com/spearchucker667/venice-media-skill.git
Set-Location .\venice-media-skill
.\scripts\install.ps1 -HostName kimi -Scope user
```

### Install from a release wheel

```bash
python -m pip install venice_media_skill-0.1.0-py3-none-any.whl
venice-media install-skill --host kimi --scope user
```

The wheel embeds the complete Skill and pinned API references. The `install-skill` command can also install project-local discovery directories with `--scope project --project-dir PATH`.

### Development install

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
venice-media install-skill --host kimi --scope user
```

## Configure the API key

Export the key in the shell that launches the host agent:

```bash
export VENICE_API_KEY='your-key'
```

For Zsh, place the export in a secure credential loader or keychain-backed shell integration—not directly in a public dotfile or repository.

PowerShell session:

```powershell
$env:VENICE_API_KEY = 'your-key'
```

Verify:

```bash
venice-media doctor --online
```

## Kimi Code usage

Kimi Code discovers directory-form skills from `~/.kimi-code/skills/` and `~/.agents/skills/`. Start a new Kimi session after installation, then invoke:

```text
/skill:venice-media using Venice, create an image of a sunset
```

Kimi remains the active agent. It calls `venice-media plan image.generate`, asks a grouped model-aware clarification, writes a request manifest, and executes the bridge.

A typical clarification may be:

> Which Venice image model should be used, and do you want the model defaults or custom dimensions/aspect ratio, CFG, negative prompt, steps, seed, variants, and output format?

The exact questions depend on the selected model's current `/models` constraints.

## CLI contract

### Health check

```bash
venice-media doctor
venice-media doctor --online
```

### Discover models

```bash
venice-media models --type image
venice-media models --type video --refresh
venice-media models --type tts
venice-media models --type music
```

### Plan questions

```bash
venice-media plan image.generate --prompt 'A sunset over the Pacific'
venice-media plan image.generate --model 'MODEL_ID' --prompt 'A sunset over the Pacific'
venice-media plan video.generate --model 'MODEL_ID' --prompt 'A slow aerial reveal at dusk'
```

### Validate a request without spending credits

Set `execution.dry_run` to `true`, then:

```bash
venice-media run examples/requests/image-generate.json
```

The result includes the endpoint and exact redacted API payload.

### Execute a request

```bash
venice-media run request.json
```

Status values:

| Status | Meaning |
|---|---|
| `completed` | Media or transcript was written locally. |
| `approval_required` | A quote was obtained; set `confirmed_cost=true` only after approval. |
| `queued` | Job was queued without waiting. Preserve the queue ID. |
| `processing` | Retrieval found the job still running. |
| `timed_out` | Local wait ended; retrieve the existing queue ID later. |
| `consent_required` | Seedance face-media policy text must be explicitly accepted. |
| `dry_run` | No API call was made. |
| `error` | Validation, local I/O, or Venice API failure. |

### Inspect queued jobs

```bash
venice-media jobs list
venice-media jobs get QUEUE_ID
```

Queue records are stored in the platform-specific state directory returned by `venice-media doctor`.

## Request manifest

```json
{
  "version": "1",
  "operation": "image.generate",
  "model": "MODEL_FROM_LIVE_CATALOG",
  "prompt": "A cinematic sunset over a glass-calm ocean",
  "parameters": {
    "width": 1024,
    "height": 1024,
    "negative_prompt": "text, logos, artifacts",
    "variants": 1,
    "format": "webp"
  },
  "inputs": {},
  "output": {
    "directory": "./venice-media-output",
    "filename": "sunset.webp",
    "overwrite": false,
    "write_metadata": true
  },
  "execution": {
    "dry_run": false,
    "quote_first": false,
    "confirmed_cost": false,
    "wait": true,
    "poll_interval_seconds": 5,
    "timeout_seconds": 900,
    "delete_remote_on_completion": false
  },
  "attestations": {
    "seedance_face_consent": false
  }
}
```

The complete JSON Schema is at `references/request.schema.json` and can be emitted with:

```bash
venice-media schema
```

## Output and metadata

Default artifacts are written to `./venice-media-output/`. Override with:

```bash
export VENICE_MEDIA_OUTPUT_DIR="$HOME/Media/Venice"
```

Each generated artifact can receive a sidecar such as:

```text
sunset.webp
sunset.webp.json
```

The sidecar does not contain the API key. It records reproducibility and debugging information, including ignored or rejected parameters when surfaced by the API.

## Seedance 2.0

The package includes dedicated references for:

- Text-to-video
- Image-to-video
- Reference-to-video
- Reference, Edit, Extend, and Stitch prompt routing
- Reference images, videos, and audio donors
- Face-media consent, deduplication, and revocation

Seedance workflows are selected by canonical prompt syntax, not by a separate `workflow` field. See [`docs/media-generation-guide.md`](docs/media-generation-guide.md).

## Security boundary

The bridge deliberately does not:

- Store API keys
- Read arbitrary directories
- Upload files that were not explicitly named in a request
- Forward the Venice authorization header to pre-signed third-party media URLs
- Automatically retry by submitting duplicate paid generation jobs
- Auto-attest rights to human likenesses
- Replace provider error messages with fabricated success

See [`docs/security-and-privacy.md`](docs/security-and-privacy.md) and [`SECURITY.md`](SECURITY.md).

## Validation

```bash
./scripts/validate.sh
```

Equivalent commands:

```bash
python -m compileall -q src
ruff check .
ruff format --check .
mypy src
pytest --cov=venice_media_skill --cov-report=term-missing
python -m build
venice-media validate-openapi references/venice-openapi.yaml
```

## API snapshot provenance

The bundled OpenAPI file records:

- Source: `https://api.venice.ai/doc/api/swagger.yaml`
- Retrieved: `2026-07-11`
- API content version: `20260709.204640`

Runtime model selection still uses live `GET /models`. Refresh the snapshot before releases with an authorized and reviewed update process; do not silently overwrite it during normal CLI execution.

## Documentation

- [Architecture](docs/architecture.md)
- [Agent workflow](docs/agent-workflow.md)
- [Media generation guide](docs/media-generation-guide.md)
- [Host integrations](docs/host-integrations.md)
- [Security and privacy](docs/security-and-privacy.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Release process](docs/releasing.md)

## License

MIT. Venice API documentation and trademarks remain the property of their respective owners. See `THIRD_PARTY_NOTICES.md`.
