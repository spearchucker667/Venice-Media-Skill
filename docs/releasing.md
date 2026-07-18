# 🚀 Release Process

*Back to: [README.md](../README.md) | [CHANGELOG.md](../CHANGELOG.md)*

---

This document describes the release process for Venice Media Skill. Releases are automated via GitHub Actions, but require manual preparation and validation.

---

## ✅ Pre-Release Checklist

### 1. 📚 Documentation & API Snapshot

- [ ] **Refresh Venice OpenAPI snapshot** or intentionally retain existing version
- [ ] **Record provenance** in the snapshot file:
  - Source URL
  - Retrieval date
  - API content version
- [ ] **Review API changes** affecting:
  - Request schemas
  - Queue responses
  - Consent flows
  - Model metadata

### 2. 📝 Version Updates

- [ ] **Update `CHANGELOG.md`** with new version entry
- [ ] **Update version** in `pyproject.toml`
- [ ] **Update version** in `src/venice_media_skill/__init__.py`
- [ ] **Update version** in both Kimi plugin manifests
- [ ] **Verify consistency** across all version references

### 3. ⚡ Validation

- [ ] **Run full validation suite** on multiple Python versions:
  ```bash
  ./scripts/validate.sh
  ```
- [ ] **Test on Python 3.11** (minimum supported)
- [ ] **Test on at least one newer Python version** (3.12, 3.13, or 3.14)

### 4. 📦 Installation Testing

- [ ] **Test install scripts** on:
  - [ ] macOS
  - [ ] Linux
  - [ ] Windows PowerShell
- [ ] **Verify successful installation** on each platform

### 5. 🧪 Smoke Testing

- [ ] **Run all dry-run examples** for each supported operation:
  - Image generation
  - Image editing
  - Video generation
  - TTS
  - Audio transcription
  - Music generation
- [ ] **Run one authorized smoke test** per changed media surface
- [ ] **Verify all tests pass** with `pytest`

### 6. 🔒 Security Audit

- [ ] **Verify artifacts and sidecars** contain no credentials
- [ ] **Check for accidentally committed secrets**
- [ ] **Ensure no API keys** are bundled
- [ ] **Confirm no `.env` files** are included
- [ ] **Verify no virtual environment files** are bundled
- [ ] **Check no local queue state** is included
- [ ] **Confirm no model cache** is bundled
- [ ] **Ensure no generated media** is committed

---

## 🏗️ Build Process

### 1. Build Distributions

```bash
# Clean any previous builds
rm -rf dist/ build/

# Build wheel and source distribution
python -m build --wheel --sdist

# Verify artifacts exist
ls -la dist/
```

### 2. Inspect Distribution Contents

```bash
# Unpack and inspect wheel
unzip -l dist/venice_media_skill-*.whl

# Unpack and inspect source distribution
tar -tzf dist/venice_media_skill-*.tar.gz

# Verify no sensitive files are included
# Check for: .env, .venv, __pycache__, *.pyc, *.key, etc.
```

---

## 📦 Publishing

### 1. Create Git Tag

```bash
# Create annotated tag
git tag -a vX.Y.Z -m "Release vX.Y.Z"

# Push tag to origin
git push origin vX.Y.Z
```

### 2. GitHub Actions Automation

- GitHub Actions workflow (`.github/workflows/release.yml`) automatically:
  1. Triggers on new tags matching `v*` pattern
  2. Verifies tag matches package version (via `verify-release.py`)
  3. Builds wheel and source distribution
  4. Creates GitHub Release
  5. Attaches artifacts to the release

### 3. Manual Verification

- [ ] **Verify GitHub Release** was created successfully
- [ ] **Confirm artifacts** are attached to the release
- [ ] **Check release notes** are populated from CHANGELOG
- [ ] **Test installation from wheel or sdist** (if published)

---

## ❌ Prohibited Actions

**NEVER do the following:**

- ❌ Publish from a dirty working tree
- ❌ Bundle `.env` files
- ❌ Bundle virtual environments
- ❌ Bundle local queue state
- ❌ Bundle model cache
- ❌ Bundle generated media
- ❌ Bundle API keys or credentials
- ❌ Skip validation steps
- ❌ Release without CHANGELOG updates
- ❌ Release without version updates

---

## 🎯 Release Types

| Type | Version Bump | When to Use |
|------|--------------|-------------|
| **MAJOR** | `X.0.0` | Breaking changes, API incompatibilities |
| **MINOR** | `0.Y.0` | New features, backwards-compatible |
| **PATCH** | `0.0.Z` | Bug fixes, backwards-compatible |

---

## 📊 Release Statistics

Track release metrics:

| Metric | Command/Location |
|--------|------------------|
| Version | `pyproject.toml`, `__init__.py` |
| Files | `git ls-files` |
| Lines of code | `cloc src/` |
| Test coverage | `pytest --cov-report=term-missing` |
| Artifact sizes | `ls -la dist/` |

---

## 🔄 Post-Release

### 1. Announcement

- [ ] **Create GitHub Release** (if not automated)
- [ ] **Update GitHub Discussions** with release announcement
- [ ] **Post to community channels** (if applicable)

### 2. Version Bump for Next Cycle

```bash
# Bump version for next development cycle
# e.g., from 1.2.1 to the next intended semantic version
```

### 3. Monitor

- [ ] **Watch for issues** related to the new release
- [ ] **Address critical bugs** with patch releases if needed

---

## 🤖 Automation

The release process is partially automated via:

- **GitHub Actions** (`.github/workflows/release.yml`) - Build and release automation
- **Build Script** (`python -m build`) - Wheel and sdist generation
- **Validation Script** (`./scripts/validate.sh`) - Pre-release validation

---

## 📚 Related Documentation

- [CHANGELOG](../CHANGELOG.md) - Version history and changes
- [Contributing Guide](../CONTRIBUTING.md) - Development guidelines
- [Validation Script](../scripts/validate.sh) - Pre-release checks

---

<div align="center">

[⬅️ Back to README](../README.md) | [Top](#-release-process)

</div>
