# Security policy

## Supported versions

Security fixes are applied to the latest released minor version.

## Reporting

Do not open a public issue for exposed credentials, authorization-header leakage, arbitrary-file upload, path traversal, consent bypass, queue duplication that can cause repeated charges, or malicious output execution.

Use GitHub's private vulnerability reporting feature when enabled, or contact the repository maintainer privately.

Include reproduction steps, affected version, operating system, sanitized logs, and impact. Never include a live Venice API key or personal face media.

## Security invariants

- API keys come only from the environment.
- Credentials are not forwarded to pre-signed download hosts.
- Explicit local paths are required for uploads.
- Seedance consent requires user confirmation.
- Timed-out jobs are retrieved, not automatically resubmitted.
- API and media output are treated as untrusted data.
