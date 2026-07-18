# Venice Media Skill - Threat Model

**Version:** 1.2.0  
**Last Updated:** 2026-07-18  
**Status:** Active  
**Classification:** Public  

---

## 📋 Document Overview

This document provides a comprehensive threat model for the Venice Media Skill package, identifying trusted and untrusted boundaries, potential attack vectors, and implemented security controls. It is intended for:

- **Users** deploying the skill in production environments
- **Developers** contributing to the codebase
- **Security reviewers** performing audits
- **AI agent operators** integrating the skill

---

## 🎯 System Overview

### Purpose
Venice Media Skill is a host-neutral Python bridge that enables AI CLI agents (Kimi Code, Claude Code, Gemini CLI, etc.) to utilize Venice AI's media generation APIs without replacing the host agent's reasoning capabilities.

### Architecture
```
┌─────────────────────────────────────────────────────────────────┐
│                        AI Host Agent                              │
│  (Kimi Code, Claude Code, Gemini CLI, OpenCode, etc.)             │
└────────────────────┬────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Venice Media Skill CLI                          │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────┐  │
│  │ Request      │  │ Response     │  │ Job Store &            │  │
│  │ Manifest     │  │ Processing   │  │ Artifact Persistence   │  │
│  │ Validation   │  │ Handling     │  │                        │  │
│  └──────────────┘  └──────────────┘  └────────────────────────┘  │
└────────────────────┬────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Venice AI API                               │
│  (External Service - https://api.venice.ai)                        │
└─────────────────────────────────────────────────────────────────┘
```

### Key Components

| Component | Description | Trust Level |
|-----------|-------------|-------------|
| Host Agent | External AI CLI that invokes the skill | Untrusted Input Source |
| Request Parser | Validates and parses JSON manifests | Trusted |
| Venice Client | HTTP client for API communication | Trusted |
| Media Runner | Dispatches operations to appropriate handlers | Trusted |
| Artifact Writer | Saves media and metadata to filesystem | Trusted |
| Job Store | Persistent storage for queue state | Trusted |

---

## 🔐 Trust Boundaries

### Trusted Components
The following are considered **trusted** within the Venice Media Skill:

1. **Local Codebase** - All Python code in this repository
2. **Environment Configuration** - Environment variables set by the user
3. **Venice API** - The remote Venice service at https://api.venice.ai
4. **Local Filesystem** - Areas where the user has explicit write permissions

### Untrusted Inputs
The following are considered **untrusted** and must be validated:

#### 1. **Agent-Generated Request Manifests**
- **Source:** AI host agents constructing JSON requests
- **Risk:** Malicious prompts, prompt injection, or compromised agents can generate arbitrary JSON
- **Examples:** `operation`, `parameters`, `inputs`, `output` fields
- **Mitigation:** Schema validation, parameter sanitization, path validation

#### 2. **Provider API Responses**
- **Source:** Venice API responses
- **Risk:** Compromised provider, man-in-the-middle, or misconfigured endpoints
- **Examples:** `download_url`, `url`, binary content, JSON payloads
- **Mitigation:** HTTPS enforcement, URL validation, content-type verification

#### 3. **User-Supplied Configuration**
- **Source:** CLI arguments, configuration files
- **Risk:** User error, compromised configuration
- **Examples:** `output.directory`, API key references
- **Mitigation:** Path validation, directory containment checks

#### 4. **Local File Inputs**
- **Source:** Files referenced in manifests
- **Risk:** Symlink attacks, malicious file content
- **Examples:** Image files for editing, audio for transcription
- **Mitigation:** Content validation, size limits, magic-byte verification

#### 5. **Network Resources**
- **Source:** URLs referenced in responses or manifests
- **Risk:** SSRF, data exfiltration, malicious content
- **Examples:** `download_url`, `url` fields
- **Mitigation:** URL allowlisting, IP blocking, protocol restrictions

---

## 🎯 Attack Surface Analysis

### 1. Input Validation Attacks

#### VMS-001: Path Traversal (FIXED)
- **Vector:** Malicious `output.filename` containing `../` or absolute paths
- **Impact:** Arbitrary file overwrite on local filesystem
- **Status:** ✅ FIXED with `_validate_safe_filename()` and path containment checks
- **Control:** Reject absolute paths, path separators, traversal sequences, null bytes, drive letters, UNC paths

#### VMS-002: SSRF - Server-Side Request Forgery (FIXED)
- **Vector:** Malicious `download_url` pointing to internal services
- **Impact:** Access to local services, cloud metadata, internal networks
- **Status:** ✅ FIXED with HTTPS enforcement, IP validation, redirect validation
- **Control:** Block loopback, private, link-local, multicast, reserved IPs; validate redirects

#### VMS-015: Base64 Misclassification
- **Vector:** Arbitrary strings divisible by 4 classified as base64
- **Impact:** Potential processing of malicious data
- **Status:** ✅ FIXED
- **Control:** Decoded base64/JSON artifacts re-validated through `fast_validate_content_type`; magic-byte verification is fail-closed

#### VMS-016: MIME Type Inference
- **Vector:** Filename-based MIME type guessing
- **Impact:** Incorrect content handling, potential bypass
- **Status:** ✅ FIXED
- **Control:** Content-based type detection via magic-byte validation; unknown or mislabeled content is rejected

### 2. Authorization and Access Control

#### VMS-005: Consent Bypass
- **Vector:** Pre-populated `attestations.seedance_face_consent` boolean
- **Impact:** Bypassing provider consent requirements
- **Status:** ✅ FIXED
- **Risk:** LOW
- **Control:** Challenge-response flow with hash-bound approval store, persisted consent challenges, explicit `venice-media approve-consent` command

### 3. API Abuse

#### VMS-003: Incorrect Model Field
- **Vector:** Using `model` instead of `modelId` for image edit
- **Impact:** Silent fallback to default model or rejection
- **Status:** ✅ FIXED
- **Control:** Contract alignment tests enforce canonical field names; payload builders consume `allowed_parameter_names()` from the request contract authority

#### VMS-004: Undocumented Parameter Names
- **Vector:** Using `creativity` instead of `enhanceCreativity` for upscale
- **Impact:** Parameters silently ignored or rejected
- **Status:** ✅ FIXED
- **Control:** Per-operation contracts enforce exact parameter names; upscale uses `creativity` + `scale` only

### 4. Resource Exhaustion

#### VMS-007: Unbounded Downloads
- **Vector:** Large media responses loaded entirely into memory
- **Impact:** Memory exhaustion, process termination
- **Status:** ✅ FIXED with streaming and size limits
- **Control:** 500MB maximum download, streaming implementation

### 5. Data Integrity

#### VMS-008: Content-Type Trust
- **Vector:** Trusting `Content-Type` header for file extension
- **Impact:** Malicious content saved with wrong extension
- **Status:** ✅ FIXED
- **Control:** Magic-byte verification (`fast_validate_content_type`) is fail-closed for every supported media type; decoded base64/JSON artifacts are re-validated

#### VMS-006: Completed Response URL Handling
- **Vector:** URL returned only at completion not inspected
- **Impact:** Media unavailable despite being ready
- **Status:** ✅ FIXED
- **Control:** All URL fields in polling responses are inspected; redirects are validated before follow

### 6. Concurrency Issues

#### VMS-013: Non-Atomic Writes
- **Vector:** Direct writes to final paths
- **Impact:** Truncated media, missing metadata, inconsistent state
- **Status:** ✅ FIXED
- **Control:** Write to temp files, fsync, atomic rename; EXDEV fallback validates size and SHA before cross-filesystem replace

#### VMS-014: Race-Prone Collision Handling
- **Vector:** Check-then-write pattern for file existence
- **Impact:** Concurrent executions overwrite each other
- **Status:** ✅ FIXED
- **Control:** Unique sibling temp files, per-record exclusive locks, UUID-based names

---

## 🛡️ Security Controls Matrix

### ✅ Implemented Controls

| Control | Description | Status | Reference |
|---------|-------------|--------|-----------|
| Path Validation | Filename safety checks prevent traversal | ✅ Implemented | VMS-001 |
| Path Containment | Resolved path must remain within output directory | ✅ Implemented | VMS-001 |
| HTTPS Enforcement | Only HTTPS URLs allowed for downloads | ✅ Implemented | VMS-002 |
| IP Blocking | Block loopback, private, link-local, multicast, reserved | ✅ Implemented | VMS-002 |
| Redirect Validation | Validate redirect target URLs per hop | ✅ Implemented | VMS-002 |
| Size Limits | Maximum 500MB download size, streaming with byte cap | ✅ Implemented | VMS-007 |
| Credential Isolation | API key from environment only, never in manifests | ✅ Implemented | Design |
| Queue Persistence | Local storage of queue IDs for recovery | ✅ Implemented | Design |
| Content Validation | Magic-byte verification fail-closed for all supported types | ✅ Implemented | VMS-008, VMS-015, VMS-016 |
| Parameter Validation | Operation-specific schema validation with per-operation contracts | ✅ Implemented | VMS-009 |
| Consent Challenge Flow | Two-stage consent with challenge-response and hash-bound approval | ✅ Implemented | VMS-005 |
| API Field Compatibility | Contract alignment tests enforce canonical field names | ✅ Implemented | VMS-003, VMS-004 |
| Completed URL Discovery | All URL fields inspected in polling responses | ✅ Implemented | VMS-006 |
| Atomic Writes | Temp file + fsync + atomic rename pattern | ✅ Implemented | VMS-013 |
| Concurrency Control | Per-record exclusive locks, UUID filenames | ✅ Implemented | VMS-014 |
| Quote Gate Enforcement | Manifest-controlled bypasses removed; single-use hash-bound approval | ✅ Implemented | VMS-002 |
| Transport Error Distinction | Typed errors with exit code 9 for transport failures | ✅ Implemented | VMS-009 |
| Cross-Filesystem Output | EXDEV fallback with size and SHA validation | ✅ Implemented | VMS-010 |

### ❌ Missing Controls

All identified block-release and high-severity findings have been remediated. No remaining critical or high-severity controls are missing.

---

## 🎭 Threat Scenarios

### Scenario 1: Compromised AI Agent
**Actor:** Malicious or compromised host agent  
**Vector:** Generates malicious request manifest  
**Target:** Local filesystem, network services  
**Controls:** Path validation (VMS-001), SSRF protection (VMS-002)  
**Status:** ✅ Protected against known vectors

### Scenario 2: Malicious Prompt Injection
**Actor:** User or attacker injecting malicious prompts  
**Vector:** Prompt causes agent to generate harmful manifest  
**Target:** Local filesystem, API credentials, network  
**Controls:** Input validation, credential isolation  
**Status:** ✅ Protected with current controls

### Scenario 3: Compromised Venice Provider
**Actor:** Compromised Venice API or MITM attacker  
**Vector:** Returns malicious URLs or content  
**Target:** Local filesystem, user data  
**Controls:** URL validation, content-type verification, fail-closed magic bytes  
**Status:** ✅ Protected with current controls

### Scenario 4: Local Privilege Escalation
**Actor:** Attacker with local access  
**Vector:** Modifies job store or output directory  
**Target:** Other users' data, system files  
**Controls:** Path containment, file permissions  
**Status:** ✅ Protected with containment checks

### Scenario 5: Resource Exhaustion Attack
**Actor:** Attacker causing large downloads  
**Vector:** SSRF to attacker-controlled server returning large response  
**Target:** Process memory, disk space  
**Controls:** Size limits, streaming  
**Status:** ✅ Protected with 500MB limit

---

## 📊 Risk Assessment

### Overall Risk Rating: **LOW-MEDIUM**

| Category | Rating | Rationale |
|----------|--------|-----------|
| **Path Traversal** | ✅ LOW | Fixed with comprehensive validation |
| **SSRF** | ✅ LOW | Fixed with IP/DNS validation, per-hop redirect validation, fail-closed DNS |
| **Consent Bypass** | ✅ LOW | Fixed with challenge-response flow and hash-bound approval store |
| **API Abuse** | ✅ LOW | Fixed with per-operation contracts and contract alignment tests |
| **Resource Exhaustion** | ✅ LOW | Fixed with streaming, byte caps, and size limits |
| **Data Integrity** | ✅ LOW | Fixed with fail-closed magic-byte verification and atomic writes |
| **Concurrency** | ✅ LOW | Fixed with per-record exclusive locks and unique temp files |

### Release Readiness

| Requirement | Status | Blocking |
|-------------|--------|----------|
| P0 (Critical) | ✅ Complete | No |
| P1 (High) | ✅ Complete | No |
| P2 (Medium) | ✅ Complete | No |
| P3 (Low) | ✅ Complete | No |
| Security Tests | ✅ Complete | No |
| Documentation | ✅ Complete | No |

**Conclusion:** The package is ready for public release. All identified block-release and high-severity findings across P0, P1, and P2 have been remediated and validated. Residual risks are documented in the threat model's Known Limitations section and the audit remediation report.

---

## 🔧 Security Configuration

### Environment Variables

| Variable | Purpose | Security Level | Validation |
|----------|---------|----------------|------------|
| `VENICE_API_KEY` | API authentication | SECRET | Required, non-empty |
| `VENICE_BASE_URL` | API endpoint override | CONFIG | HTTPS required, requires `--allow-noncanonical-endpoint` flag |

### Filesystem Permissions

- **Output Directory:** User-writable, validated for containment
- **Job Store:** User-writable JSON files
- **Cache Directory:** User-writable, temporary files
- **Configuration:** User-readable environment variables

### Network Access

- **Allowed Protocols:** HTTPS only (no HTTP, no FTP, no custom protocols)
- **Allowed IP Ranges:** Public Internet only (no private networks)
- **Allowed Hosts:** Any valid DNS hostname (subject to IP validation)
- **Redirects:** Followed with validation of target

---

## 🚨 Incident Response

### Security Issue Reporting

If you discover a security vulnerability:

1. **DO NOT** create a public GitHub issue
2. **DO** use GitHub's Private Vulnerability Reporting or contact repository maintainers privately
3. **Include:** Steps to reproduce, impact assessment, suggested fix
4. **Expect:** Response within 48 hours, fix within 7 days for critical issues

### Security Update Process

1. **Triage:** Assess severity and impact
2. **Fix:** Develop and test remediation
3. **Test:** Add regression tests
4. **Disclose:** Release fixed version with security advisory
5. **Notify:** Inform users of required actions

---

## 📝 Compliance and Standards

### Security Standards
- OWASP Top 10 (2021)
- CWE/SANS Top 25 Most Dangerous Software Weaknesses
- NIST SSDF (Secure Software Development Framework)

### Applicable Controls
- **CWE-22:** Improper Limitation of a Pathname to a Restricted Directory (VMS-001)
- **CWE-918:** Server-Side Request Forgery (SSRF) (VMS-002)
- **CWE-284:** Improper Access Control (VMS-005)
- **CWE-20:** Improper Input Validation (Multiple)
- **CWE-770:** Allocation of Resources Without Limits (VMS-007)

### Data Protection
- **API Keys:** Never logged, never written to disk
- **Prompts:** Stored in metadata sidecars (configurable)
- **Media Content:** Written to user-specified directories only
- **Network Data:** Transmitted over HTTPS only

---

## 🔄 Maintenance and Updates

### Dependency Security
- Regular dependency updates via Dependabot
- Security advisory monitoring
- Pinning of critical dependencies

### Code Review
- All changes require peer review
- Security-sensitive changes require security review
- Regression tests mandatory for security fixes

### Testing
- Unit tests for all security controls
- Integration tests for end-to-end flows
- Fuzz testing for input validation
- Penetration testing for new features

---

## 📞 Contacts

| Method | Details |
|--------|---------|
| GitHub Issues | [Report bugs](https://github.com/spearchucker667/venice-media-skill/issues) |
| Security Reports | [Private vulnerability reporting](https://github.com/spearchucker667/venice-media-skill/security) (preferred) or [SECURITY.md](SECURITY.md) |

---

## 📚 References

1. [Venice API Documentation](https://docs.venice.ai)
2. [OWASP Threat Modeling](https://owasp.org/www-community/Threat_Modeling)
3. [CWE/SANS Top 25](https://cwe.mitre.org/top25/)
4. [NIST SSDF](https://csrc.nist.gov/projects/ssdf)
5. [Security Audit Report](https://github.com/spearchucker667/venice-media-skill/security)

---

## ⚠️ Known Limitations of the Current SSRF Protection

The bridge follows a **permit-by-allow-list** strategy rather than IP pinning. The following gaps are mitigated *today* but each one is documented because it would block a stricter audit.

### TOCTOU / DNS rebinding window

`_enforce_safe_target()` performs a `_resolve_safely()` lookup and rejects private / loopback / link-local / reserved / metadata IPs. The hop then issues its HTTP request, and `httpx` re-resolves the hostname when it opens the socket. An attacker who can flip the answer between the safety check and connect can land on a different IP than the one that was validated.

**What protects us today:** the host allow-list is narrow (`api.venice.ai` for authenticated calls; `cdn.venice.ai`, `venice.ai`, `storage.googleapis.com`, `r2.cloudflarestorage.com`, `media.venice.ai`, plus the `.venice.ai` operator suffix for downloads) and every redirect is re-validated against it.

**What does not protect us today:** a published URL that resolves to attacker-controlled infrastructure outside the allow-list. Until IP pinning through a custom transport lands (open follow-up), the bridge trusts the allow-list to enumerate Venice's authoritative surface and trusts DNS to match the request URL to that surface.

### No IP pinning

The current `httpx.Client.stream("GET", current)` call connects by hostname. We do not pin the validated IP. A successful harness against the current code is straightforward: spin up a stub HTTP transport whose `handle_request` records the resolved IP and assert the bridge never re-resolves through it.

### Authenticated-client proxy stance

`VeniceClient.__init__` constructs the authenticated `httpx.Client` with the httpx defaults (which **do** honor `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY`). The public-download path uses `httpx.Client(trust_env=False)`. The split is deliberate: a node already spokes for `api.venice.ai` through its user's proxy may be acceptable, but a signed media URL must never be coerced through a third party. Operators expecting strict proxy bypass on authenticated traffic should pass a custom `httpx.BaseTransport` that constructs `httpx.Client(transport=…, follow_redirects=False)` directly.

### Content validation covers only the response head

`fast_validate_content_type` validates the first ~4 KiB of the body. Bodies whose declared `Content-Type` is honest through the head but diverges later (e.g., a PNG header wrapped around an executable payload) are not currently block-streamed end-to-end. The `_MemorySink` / `_FileSink` plumbing makes full-body content re-validation a low-cost follow-up.

### Cloud-host suffixes were intentionally narrowed

Earlier drafts allowed `*.amazonaws.com`, `*.cloudflarestorage.com`, `*.googleapis.com` as download allow-list suffixes. These admit unrelated tenants and defeat SSRF contract review. The current `ALLOWED_HOST_SUFFIXES = (".venice.ai",)` reduces this surface to operator-only subdomains.

### Uncensored content ≠ Seedance face-consent waiver

`safe_mode=false` is an output-moderation knob returned to Venice at submit time. It is independent of the Seedance `409 needs_consent` legal attestation. A certificate of `safe_mode=false` does not authorize likeness generation; the explicit `attestations.seedance_face_consent=true` flag still requires the user to confirm the policy text returned in the 409 payload.

---

**Document Version History**

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0.0 | 2026-07-16 | Security Audit | Initial threat model based on comprehensive audit |
| 1.1.0 | 2026-07-17 | Hardening sweep | Documented P0/P1 fixes (host separation, true streaming, resolver injection, in-memory vs file-mode defaults, typed `PublicHttpError`), added Known Limitations of the current SSRF protection, re-assessed VMS-005/007/008/013. |
| 1.2.0 | 2026-07-18 | Remediation audit | All 28 VMS findings (VMS-001 through VMS-028) remediated; consent, quote, payload, download, transport, concurrency, and CI controls verified. Release readiness achieved.

---

*This document is maintained by the Venice Media Skill security team. For questions or concerns, please refer to the security policy.*
