# 🛡️ Security Policy

*Back to: [README.md](README.md) | [CONTRIBUTING.md](CONTRIBUTING.md) | [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)*

---

## 📋 Supported Versions

Security fixes and patches are applied to the **latest released minor version**. Older versions may not receive security updates.

| Version | Status | Security Support |
|---------|--------|-------------------|
| `>= 0.1.0` | ✅ Active | Security fixes included |
| `< 0.1.0` | ❌ Legacy | No security support |

---

## 🚨 Reporting Security Vulnerabilities

**Do NOT open a public issue** for security vulnerabilities, including:

- ❌ Exposed credentials or API keys
- ❌ Authorization header leakage
- ❌ Arbitrary file upload vulnerabilities
- ❌ Path traversal attacks
- ❌ Consent bypass mechanisms
- ❌ Queue duplication causing repeated charges
- ❌ Malicious output execution

### How to Report

1. **Use GitHub's Private Vulnerability Reporting** (when enabled)
2. **Contact maintainers privately** via secure channels
3. **Include the following information:**
   - Reproduction steps
   - Affected version(s)
   - Operating system
   - Sanitized logs (remove all credentials)
   - Impact assessment
   - Suggested mitigation (if known)

> ⚠️ **IMPORTANT:** Never include live Venice API keys, personal face media, or actual user data in vulnerability reports.

---

## 🔒 Security Invariants

The Venice Media Skill enforces these **non-negotiable** security guarantees:

### ✅ Enforced Protections

| Invariant | Description | Implementation |
|-----------|-------------|----------------|
| **Environment-only credentials** | API keys are **only** read from process environment | `config.py`, `client.py` |
| **No credential forwarding** | Venice auth headers are **never** forwarded to pre-signed download hosts | `client.py` |
| **Explicit paths required** | All file uploads require explicit local paths | `request.py` validation |
| **Consent gate** | Seedance face-media consent requires explicit user confirmation | `runner.py` consent handling |
| **No auto-retry spend** | Timed-out jobs are **retrieved**, never automatically resubmitted | `jobs.py`, `runner.py` |
| **Untrusted data handling** | API and media outputs are treated as untrusted | All I/O processing |

### 🔍 Security Review Checklist

Before merging changes, verify:

- [ ] No credentials or API keys are logged or stored
- [ ] No arbitrary file system access beyond explicit inputs
- [ ] Environment variable handling is secure
- [ ] Error messages don't leak sensitive information
- [ ] All external data is validated before use
- [ ] Queue management prevents duplicate charges
- [ ] Consent requirements are properly enforced

---

## 📚 Related Documentation

- [Security & Privacy Guide](docs/security-and-privacy.md) - Detailed security best practices
- [Architecture](docs/architecture.md) - System trust zones and boundaries
- [Threat Model](docs/threat-model.md) - Comprehensive threat analysis and risk assessment
- [Troubleshooting](docs/troubleshooting.md) - Common security-related issues

---

<div align="center">

[⬅️ Back to README](README.md) | [Top](#-security-policy)

</div>
