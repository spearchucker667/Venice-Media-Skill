## Scope

Describe the problem and the exact behavior changed.

## Validation

- [ ] `python -m ruff check .`
- [ ] `python -m ruff format --check .`
- [ ] `python -m mypy src`
- [ ] `python -m pytest --cov=venice_media_skill --cov-report=term-missing`
- [ ] `python -m build`
- [ ] `python -m venice_media_skill validate-openapi references/venice-openapi.yaml`
- [ ] No credentials, private prompts, generated face media, or large generated artifacts were committed.

## Compatibility

List affected hosts, platforms, operations, manifest fields, and migration requirements.
