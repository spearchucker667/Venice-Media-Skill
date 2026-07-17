# 📜 Changelog

*Back to: [README.md](README.md)*

---

All notable changes to this project follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. This project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## 📦 [Unreleased]

> Changes that have been committed but not yet released in a version.

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
