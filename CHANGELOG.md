# Changelog

All notable changes to this project follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. This project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Changes that have been committed but not yet released.

## [1.3.0] - 2026-08-14

### Added

- Synchronized the bundled OpenAPI snapshot with `veniceai/api-docs` at commit `db3b9f4f40fe71abff2011bcaa9c23ad797c94f3` (OpenAPI `info.version = 20260814.153445`).
- Added `scripts/sync-venice-api-docs.py` for deterministic upstream snapshot refresh and mirror synchronization.
- Added `.github/workflows/api-drift.yml` for weekly upstream drift detection.
- Added `tests/test_api_contracts.py` covering snapshot provenance, runtime/schema parity, payload wire keys, reserved-key gating, and release metadata.
- Image generation: added `enhance_prompt` and typed `style_references` support.
- Image edit/multi-edit: added `enhance_prompt` and `disable_prompt_optimization_thinking` support.
- TTS: added `language`, `temperature`, `top_p`, and `style_prompt` (mapped to provider `prompt`) support.
- Video generation: updated reference limits to 30 images / 10 videos / 10 audio, added typed `keyframes` support.
- Audio generation: added `loop` support.
- New operations: `audio.voice_clone` and `video.transcribe`.

### Changed

- Consolidated repository documentation: rewrote `README.md` to be concise, rewrote `AGENTS.md` as high-signal agent instructions, created `docs/index.md` and `docs/api-sync.md`, rewrote `docs/api-reference-snapshot.md` to point to the exact upstream commit, and corrected `docs/releasing.md` to reflect GitHub-generated release notes.
- Moved `docs/security-post-mortem-july-2026.md` to `docs/audits/security-post-mortem-july-2026.md`.
- Hardened `.github/workflows/release.yml` with `fetch-depth: 0`, main-branch containment checks, version agreement, `twine check`, fresh-venv smoke tests, source archives, checksums, concurrency protection, and immutable action SHA pins where practical.

### Fixed

- Reconciled package version metadata with release tags; `scripts/verify-release.py` now also checks the fallback version in `src/venice_media_skill/__init__.py`.

## [1.2.0] - 2026-07-18

### Security

- Seedance consents are now bound to a persisted challenge that the agent must approve through `venice-media approve-consent`. Arbitrary `parameters.consents` is rejected at manifest validation.
- Paid queued video/audio generation now requires a hash-bound quote approval via `venice-media approve-quote`. The runner refuses to queue if the canonical payload hash, the observed cost, or the recorded maximum cost disagrees with the approval.
- Per-operation payload builders reject reserved keys (`model`, `prompt`, `consents`, `queue_id`, `download_url`, `image_url`, transport controls, …) inside `parameters`. Quote and queue payloads are derived from the same canonical hash.
- Public media URLs are validated before every redirect hop: HTTPS-only, allow-listed Venice CDN hosts, fail-closed DNS, and non-global resolved IPs are blocked.
- Streaming downloads enforce both `Content-Length` (pre-flight) and an incremental byte cap while iterating chunks, with SHA-256 computed in flight and partial temp files removed on overflow.
- Magic-byte validation is fail-closed for every supported media type.

### Architecture

- Per-operation payload builders in `venice_media_skill.payloads` are the single authority for what reaches the provider.
- Added `consent.py` and approval stores that persist challenges and approvals with hash binding.
- Added `reserved.py` constants for shared reserved/transport-control keys.
- Planner now returns `{parameters: {...}, execution: {...}}` keeping provider defaults and execution policy separated.
- Image response-mode planning maps one image to binary mode and 2–4 images to JSON mode with ordered variant metadata.

### Tests

- Added dedicated coverage for reserved-parameter rejection, SSRF-safe redirects, streamed download safety, fail-closed magic bytes, consent challenge state machine, quote approval binding, and image generate response modes.

## [0.1.0] - 2026-07-16

### Added

- Host-neutral Agent Skill compatible with any shell-capable AI agent.
- Kimi Code adapter.
- Python JSON bridge for image, video, and audio operations.
- Image operations: generate, edit, multi-edit, upscale, background removal.
- Video operations: generate, retrieve.
- Audio operations: TTS, music/audio generation, transcription.
- Live model discovery via `GET /models`.
- Model-aware planning.

### Security & Privacy

- Image generation defaults to `safe_mode=false`, `hide_watermark=true`.
- Quote approval gates for queued video/audio generation.
- Durable queue records for timeout-safe retrieval.
- Seedance 2.0 consent flow.

[Unreleased]: https://github.com/spearchucker667/Venice-Media-Skill/compare/v1.3.0...HEAD
[1.3.0]: https://github.com/spearchucker667/Venice-Media-Skill/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/spearchucker667/Venice-Media-Skill/compare/v0.1.0...v1.2.0
[0.1.0]: https://github.com/spearchucker667/Venice-Media-Skill/releases/tag/v0.1.0
