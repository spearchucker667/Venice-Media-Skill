# Release process

1. Refresh or intentionally retain the Venice OpenAPI snapshot. Record source, retrieval date, and content version.
2. Review API changes affecting request schemas, queue responses, consent, and model metadata.
3. Update `CHANGELOG.md` and package version in `pyproject.toml` and `src/venice_media_skill/__init__.py`.
4. Run `./scripts/validate.sh` on Python 3.11 and at least one newer supported interpreter.
5. Test install scripts on macOS/Linux and Windows PowerShell.
6. Run dry-run examples and one authorized smoke test per changed media surface.
7. Verify generated artifacts and sidecars contain no credentials.
8. Build wheel and source distribution.
9. Inspect distribution contents.
10. Tag `vX.Y.Z`; GitHub Actions builds and attaches artifacts to the release.

Do not publish from a dirty working tree. Do not bundle `.env`, virtual environments, local queue state, model cache, generated media, or API keys.
