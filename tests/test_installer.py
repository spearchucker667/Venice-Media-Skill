from __future__ import annotations

from pathlib import Path

import pytest

from venice_media_skill.errors import ConfigurationError
from venice_media_skill.installer import install_skill


def test_project_install_copies_complete_skill(tmp_path: Path) -> None:
    result = install_skill(host="kimi", scope="project", project_dir=str(tmp_path))
    assert result["status"] == "installed"
    # ``--host kimi`` is exclusive: only the Kimi discovery root is written.
    assert result["skill_paths"] == [str((tmp_path / ".kimi-code" / "skills" / "venice-media").resolve())]
    kimi = tmp_path / ".kimi-code" / "skills" / "venice-media"
    kimi_assets = (
        "SKILL.md",
        "references/venice-openapi.yaml",
        "references/venice-api-llms.md",
        "references/seedance-2-0-api-guide.md",
    )
    for asset in kimi_assets:
        assert (kimi / asset).is_file()
    assert not (tmp_path / ".agents").exists()


def test_project_install_all_writes_both_destinations(tmp_path: Path) -> None:
    result = install_skill(host="all", scope="project", project_dir=str(tmp_path))
    assert result["status"] == "installed"
    generic = tmp_path / ".agents" / "skills" / "venice-media"
    kimi = tmp_path / ".kimi-code" / "skills" / "venice-media"
    for destination in (generic, kimi):
        assert (destination / "SKILL.md").is_file()


def test_generic_project_install_only_writes_agents_directory(tmp_path: Path) -> None:
    install_skill(host="generic", scope="project", project_dir=str(tmp_path))
    assert (tmp_path / ".agents" / "skills" / "venice-media" / "SKILL.md").is_file()
    assert not (tmp_path / ".kimi-code").exists()


def test_install_rejects_invalid_values(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="Unsupported host"):
        install_skill(host="bad", scope="project", project_dir=str(tmp_path))
    with pytest.raises(ConfigurationError, match="Unsupported scope"):
        install_skill(host="generic", scope="bad")
    with pytest.raises(ConfigurationError, match="does not exist"):
        install_skill(host="generic", scope="project", project_dir=str(tmp_path / "missing"))
