# 🤝 Contributing

*Back to: [README.md](README.md) | [SECURITY.md](SECURITY.md) | [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)*

---

Thank you for your interest in contributing to **Venice Media Skill**! We welcome contributions from everyone.

By participating in this project, you agree to abide by the [Code of Conduct](CODE_OF_CONDUCT.md).

---

## ✅ Ground Rules

To maintain security, privacy, and quality standards, all contributors **must** follow these rules:

### ❌ Prohibited Actions

- **NO API keys** - Never commit API keys, tokens, or credentials
- **NO personal media** - Never commit generated personal media files
- **NO queue records** - Never commit local queue records or state files
- **NO `.env` files** - Never commit environment files with credentials
- **NO hard-coded catalogs** - Don't hard-code static model catalogs when live `/models` can be queried

### ✅ Required Practices

| Practice | Description |
|----------|-------------|
| **Preserve OpenAPI provenance** | Treat bundled OpenAPI as reviewed snapshot; preserve source and version |
| **Use live model data** | Prefer live `/models` data over hard-coded values |
| **Maintain protections** | Don't weaken consent, quote, redaction, or duplicate-spend protections |
| **JSON-compatible stdout** | All CLI output must be valid JSON |
| **Errors to stderr** | Operational diagnostics go to stderr, not stdout |
| **Test coverage** | Add tests for every behavior change |

---

## 🚀 Development Setup

### Prerequisites

- Python 3.11 or newer
- Git
- pip

### Setup Steps

```bash
# 1. Clone the repository
git clone https://github.com/spearchucker667/venice-media-skill.git
cd venice-media-skill

# 2. Create and activate virtual environment
python -m venv .venv

# On macOS/Linux:
source .venv/bin/activate

# On Windows:
.\.venv\Scripts\activate

# 3. Install dependencies
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'

# 4. Verify installation
venice-media --version
venice-media doctor

# 5. Run validation suite
./scripts/validate.sh
```

### IDE Configuration

For best development experience, configure your IDE to:

- Use the project's virtual environment
- Respect `.editorconfig` settings
- Run `ruff` and `mypy` on save (optional)

---

## 📝 Pull Request Process

### Before Submitting

1. **Run the full validation suite:**
   ```bash
   ./scripts/validate.sh
   ```
   This runs:
   - `python -m compileall -q src` - Syntax check
   - `ruff check .` - Linting
   - `ruff format --check .` - Format check
   - `mypy src` - Type checking
   - `pytest --cov=venice_media_skill` - Tests with coverage
   - `python -m build` - Build check
   - `venice-media validate-openapi` - OpenAPI validation

2. **Ensure all tests pass:**
   ```bash
   pytest tests/ -v
   ```

3. **Check type hints:**
   ```bash
   mypy src/
   ```

### PR Content Requirements

Every Pull Request **must** include:

| Requirement | Description |
|-------------|-------------|
| ✅ **Problem statement** | Clear description of the issue or feature |
| ✅ **Evidence/API reference** | Links to Venice API docs, error messages, or test results |
| ✅ **Behavior before/after** | What changed and why |
| ✅ **Tests added/changed** | New or modified tests covering the change |
| ✅ **Security/privacy impact** | Assessment of any security implications |
| ✅ **Manual validation** | Commands to manually verify the change |

### Additional Requirements

- **For request payload changes:** Include a dry-run example and corresponding OpenAPI reference
- **For new features:** Add documentation updates
- **For bug fixes:** Include reproduction steps
- **For breaking changes:** Document migration path

### Code Review Process

1. Maintainers will review your PR within 3-5 business days
2. All CI checks must pass
3. Security review may be required for certain changes
4. Once approved, maintainers will merge your PR

---

## 🏆 Contribution Recognition

All contributions are welcome and recognized:

- Code contributions
- Documentation improvements
- Bug reports
- Feature requests
- Security reports
- Test improvements
- CI/CD enhancements

Contributors are listed in the [GitHub Contributors](https://github.com/spearchucker667/venice-media-skill/graphs/contributors) graph.

---

## 🤔 Need Help?

- **Questions?** Open a [Discussion](https://github.com/spearchucker667/venice-media-skill/discussions)
- **Bugs?** Open an [Issue](https://github.com/spearchucker667/venice-media-skill/issues)
- **Security Issues?** See [Security Policy](SECURITY.md) for private reporting

---

## 📚 Related Documentation

- [Architecture](docs/architecture.md) - System design overview
- [Agent Workflow](docs/agent-workflow.md) - How agents use the bridge
- [Media Generation Guide](docs/media-generation-guide.md) - Media workflow documentation
- [Host Integrations](docs/host-integrations.md) - Agent setup guides

---

<div align="center">

[⬅️ Back to README](README.md) | [Top](#-contributing)

</div>
