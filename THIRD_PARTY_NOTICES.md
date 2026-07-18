# 📄 Third Party Notices

*Back to: [README.md](README.md) | [LICENSE](LICENSE)*

---

This document lists third-party components, libraries, and materials included in or used by the Venice Media Skill project.

---

## 📚 Bundled Documentation

The repository includes documentation snapshots and specifications for interoperability:

### Venice AI Materials

| Material | Source | Purpose | License |
|----------|--------|---------|---------|
| **OpenAPI Specification** | `https://api.venice.ai/doc/api/swagger.yaml` | API contract reference | Venice.ai copyright |
| **API Documentation Index** | Venice.ai documentation | Agent reference | Venice.ai copyright |
| **Seedance 2.0 Guide** | Venice.ai documentation | Video workflow reference | Venice.ai copyright |
| **Seedance Face Consent Guide** | Venice.ai documentation | Consent policy reference | Venice.ai copyright |

> **Note:** These materials originate from Venice.ai and are included for interoperability, review, and agent reference only. Their original terms, trademarks, and copyrights remain with Venice.ai and their respective owners.

### Provenance

Bundled provenance is recorded in each source file:
- **Source URL** recorded in file headers
- **Retrieval date** recorded in file headers
- **API content version** recorded in file headers

Example from `references/venice-openapi.yaml`:
```yaml
# Source: https://api.venice.ai/doc/api/swagger.yaml
# Retrieved: 2026-07-11
# API content version: 20260709.204640
```

---

## ⚖️ Disclaimer

> **This project is community tooling** and does not imply endorsement by:
> - Venice.ai
> - Moonshot AI
> - Any host-agent vendor (Kimi, Claude Code, Codex, etc.)
> - Any other third party

The use of third-party names, trademarks, or logos in this project is for identification and compatibility purposes only and does not imply endorsement.

---

## 📦 Runtime Dependencies

All runtime dependencies are declared in [`pyproject.toml`](pyproject.toml) and retain their own licenses:

| Package | Version | Purpose | License |
|---------|---------|---------|---------|
| **httpx** | >=0.28.1,<1 | HTTP client for Venice API | BSD |
| **jsonschema** | >=4.23.0,<5 | JSON Schema validation for request manifests | MIT |
| **openapi-spec-validator** | >=0.7.1,<1 | OpenAPI spec validation | Apache 2.0 |
| **platformdirs** | >=4.3.6,<5 | Platform-specific directory paths | MIT |
| **PyYAML** | >=6.0.2,<7 | YAML parsing for OpenAPI | MIT |

### Development Dependencies

| Package | Version | Purpose | License |
|---------|---------|---------|---------|
| **bandit** | >=1.8.3 | Security linting | Apache 2.0 |
| **build** | >=1.2.2.post1 | Package building | MIT |
| **mypy** | >=1.15.0 | Static type checking | MIT |
| **pip-audit** | >=2.8.0 | Dependency vulnerability scanning | MIT |
| **pytest** | >=8.3.5 | Testing framework | MIT |
| **pytest-cov** | >=6.0.0 | Test coverage reporting | MIT |
| **ruff** | >=0.11.0 | Linting and formatting | MIT |
| **types-jsonschema** | >=4.23.0,<5 | Type hints for jsonschema | MIT |
| **types-PyYAML** | >=6.0.12.20241230 | Type hints for PyYAML | MIT |

> All dependencies are available via pip and licensed under permissive open source licenses.

---

## 📋 Compliance

### User Responsibilities

When using this project, you must:

- [ ] Comply with all applicable laws and regulations
- [ ] Respect Venice.ai's API terms of service
- [ ] Honor consent requirements for face/media processing
- [ ] Protect credentials and personal data
- [ ] Not use the tool for malicious purposes

### Project Responsibilities

The project maintainers:

- [ ] Clearly identify third-party materials
- [ ] Preserve provenance information
- [ ] Respect third-party licenses and terms
- [ ] Provide clear disclaimers
- [ ] Respond to compliance inquiries

---

## 📞 Contact

For questions about third-party materials or compliance:

- Open a [Discussion](https://github.com/spearchucker667/venice-media-skill/discussions)
- Review the [LICENSE](LICENSE) file
- Check the [Security Policy](SECURITY.md) for sensitive matters

---

<div align="center">

[⬅️ Back to README](README.md) | [Top](#-third-party-notices)

</div>
