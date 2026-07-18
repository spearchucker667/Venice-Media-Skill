# 📜 Changelog

*Back to: [README.md](README.md)*

---

All notable changes to this project follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. This project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## 📦 [Unreleased]

> Changes that have been committed but not yet released in a version.

### 🔐 Security

| ID | Description | Impact |
|----|-------------|--------|
| **P0-01** | Seedance consents are now bound to a persisted challenge that the agent must approve through `venice-media approve-consent`. Arbitrary `parameters.consents` is rejected at manifest validation. | Critical |
| **P0-02** | Paid queued video/audio generation now requires a hash-bound quote approval via `venice-media approve-quote`. The runner refuses to queue if the canonical payload hash, the observed cost, or the recorded maximum cost disagrees with the approval. | Critical |
| **P0-03** | Per-operation payload builders reject reserved keys (`model`, `prompt`, `consents`, `queue_id`, `download_url`, `image_url`, transport controls, …) inside `parameters`. Quote and queue payloads are derived from the same canonical hash. | Critical |
| **P0-04** | Public media URLs are validated *before every redirect hop*: HTTPS-only, allow-listed Venice CDN hosts, fail-closed DNS, and non-global resolved IPs (loopback, private, link-local, multicast, metadata) are blocked. Authenticated API calls never follow redirects. | Critical |
| **P0-05** | Streaming downloads enforce both `Content-Length` (pre-flight) and an incremental byte cap while iterating chunks, with SHA-256 computed in flight and partial temp files removed on overflow. | Critical |
| **P0-06** | Magic-byte validation is now fail-closed for every supported media type (PNG, JPEG, RIFF+WEBP, RIFF+WAVE, MP4 ftyp, JSON, text). Unknown or mislabeled content is rejected. Decoded base64/JSON artifacts are re-validated. | Critical |

### 🧱 Architecture

| Change | Note |
|--------|------|
| Per-operation payload builders | `venice_media_skill.payloads.build_image_*`/`build_video_*`/`build_audio_*`/`build_tts`/`build_transcribe` are the single authority for what reaches the provider. |
| Consent + quote stores | New modules `consent.py` and `approval.py` persist challenges and approvals with hash binding. |
| Fail-closed downloads | `VeniceClient.download_public_url` no longer follows redirects and validates every hop before issuing the next request. |
| `reserved.py` constants | Shared set of reserved / transport-control keys that both `request.py` and `payloads.py` consult. |
| `planning` fields split | Planner now returns `{parameters: {...}, execution: {...}}` keeping provider defaults and execution policy clearly separated. Music plans emit the canonical `parameters.lyrics_prompt` and `parameters.force_instrumental` fields. |

### 🧪 Tests

| Class / test | Asserts |
|--------------|---------|
| `TestReservedParameterRejection` | All reserved keys blocked; `parameters.consents`, `parameters.model`, `parameters.prompt`, `parameters.image_url`, `parameters.download_url` reach `ReservedParameterError`. |
| `TestRedirectSafeSSRF` | HTTP, loopback, private, link-local, multicast, metadata IPs all rejected; redirect-to-loopback blocked *before* the second hop; DNS failure fail-closed. |
| `TestStreamedDownloadSafety` | Content-Length over-cap rejected before body; unbounded stream aborted at byte cap. |
| `TestFailClosedMagicBytes` | Executable/ELF/random bytes rejected; full RIFF+WEBP and RIFF+WAVE signatures required; unknown MIME rejected. |
| `TestConsentChallengeStateMachine` | Challenge persisted, recoverable, blocked-until-approval, attached-only-on-match, unacknowledged policy rejected. |
| `TestQuoteApprovalBinding` | Single-use enforcement, hash-mismatch detected, max-cost breach rejected. |
| `TestContractAlignment` | `model` (canonical) emitted, not `modelId`; upscale uses `creativity` + `scale` only; quote hash equals queue hash. |

### 📋 Audit Remediation

- Multi-artifact publication now rolls back earlier final renames and restores overwritten artifacts and metadata sidecars when a later synchronous publish step fails. Incomplete rollback retains its transaction directory and reports the recovery path.
- 2026-07-18 remediation audit tracked 28 initial findings (VMS-001 through VMS-028); a follow-up deep audit identified additional release blockers now tracked in subsequent remediation entries.
- New or changed files: `consent.py`, `errors.py`, `installer.py`, `jobs.py`, `output.py`, `payloads.py`, `planner.py`, `request.py`, `runner.py`, `util.py`, `cli.py`, `config.py`, `catalog.py`, `README.md`, `pyproject.toml`, CI workflows, release workflow, install scripts, OpenAPI snapshot, `verify-release.py`, `export-source.sh`, `verify-bundled-assets.py`.
- 260 passed, 3 skipped, 82.49% branch coverage; mypy strict for all 17 source files.

---

## 🚀 [0.1.0] - 2026-07-16

### ✨ Features Added

| Feature | Description | Impact |
|---------|-------------|--------|
| **Host-neutral Agent Skill** | Universal skill compatible with any shell-capable AI agent | Core |
| **Kimi Code Adapter** | Native integration for Kimi Code users | High |
| **Python JSON Bridge** | Complete bridge for media operations | Core |
| **Image Operations** | Generate, edit, multi-edit, upscale, background removal | High |
| **Video Operations** | Generate, retrieve, edit, extend, stitch | High |
| **Audio Operations** | TTS, music generation, transcription | High |
| **Live Model Discovery** | Dynamic `GET /models` queries | Core |
| **Model-Aware Planning** | Intelligent parameter questions based on model constraints | Core |

### 🛡️ Security & Privacy

| Feature | Description | Impact |
|---------|-------------|--------|
| **Default Safe Mode** | Image generation defaults to `safe_mode=false`, `hide_watermark=true` | Medium |
| **Quote Approval Gates** | Queued video/audio generation requires explicit cost approval | High |
| **Durable Queue Records** | Timeout-safe retrieval prevents duplicate spend | High |
| **Seedance 2.0 Consent** | Explicit face-media consent flow with policy text | Critical |

### 📚 Documentation

| Item | Description |
|------|-------------|
| **Artifact Metadata** | JSON sidecars for every media artifact with reproducibility info | High |
| **Comprehensive Tests** | Full test coverage for all modules | High |
| **CI Pipeline** | Automated testing and validation | High |
| **Release Automation** | GitHub Actions for automated releases | Medium |
| **Bundled API References** | Complete Venice API documentation snapshot | Medium |

### 🔧 Technical Details

- **Python Version:** 3.11+
- **Build System:** Hatchling
- **Type Checking:** mypy with strict mode
- **Linting:** Ruff
- **Testing:** pytest with coverage
- **Packaging:** Wheel and sdist support

---

## 📊 Release Statistics

| Version | Date | Commits | Files Changed | Lines Added | Lines Deleted |
|---------|------|---------|---------------|--------------|----------------|
| Unreleased | 2026-07-18 | 8 | 68 | 24,077 | 2,140 |
| 0.1.0 | 2026-07-16 | - | 93 | 69,172 | - |

---

## 📖 Versioning

This project adheres to [Semantic Versioning](https://semver.org/):

- **MAJOR** version: Breaking changes to API or behavior
- **MINOR** version: New features, backwards-compatible
- **PATCH** version: Bug fixes, backwards-compatible

---

## 🎯 Roadmap

### Planned Features

- [ ] Additional agent adapters (Claude Code, Gemini CLI, etc.)
- [ ] Enhanced error handling and recovery
- [ ] Performance optimizations for large media files
- [ ] Extended API coverage for new Venice features

### Deprecation Policy

- Deprecation warnings will be added in MINOR versions
- Features will be removed in the next MAJOR version
- Minimum 30 days notice for breaking changes

---

## 📚 Related Documentation

- [Release Process](docs/releasing.md) - How releases are created and published
- [Contributing Guide](CONTRIBUTING.md) - How to contribute changes
- [Architecture](docs/architecture.md) - System design and components

---

<div align="center">

[⬅️ Back to README](README.md) | [📝 Full Changelog](https://github.com/spearchucker667/venice-media-skill/commits/main)

</div>
