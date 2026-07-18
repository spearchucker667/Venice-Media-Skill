from __future__ import annotations

import json
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


def test_install_rejects_destination_that_is_a_regular_file(tmp_path: Path) -> None:
    """A destination that already exists but is not a directory must be refused."""
    bogus = tmp_path / ".kimi-code" / "skills" / "venice-media"
    bogus.parent.mkdir(parents=True)
    bogus.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="not a directory"):
        install_skill(host="kimi", scope="project", project_dir=str(tmp_path))


def test_install_rejects_destination_symlink(tmp_path: Path) -> None:
    bogus = tmp_path / ".kimi-code" / "skills" / "venice-media"
    real = tmp_path / "real-skill"
    real.mkdir()
    bogus.parent.mkdir(parents=True)
    bogus.symlink_to(real)
    with pytest.raises(ConfigurationError, match="symlink"):
        install_skill(host="kimi", scope="project", project_dir=str(tmp_path))


def test_install_refuses_orphan_backup_and_preserves_it(tmp_path: Path) -> None:
    """A previous run that crashed mid-rotate must leave its backup untouched."""
    destination = tmp_path / ".kimi-code" / "skills" / "venice-media"
    parent = destination.parent
    parent.mkdir(parents=True)
    backup = parent / ".venice-media.rollback-20260101T000000Z-deadbeef"
    backup.mkdir()
    (backup / "SKILL.md").write_text("previous-install", encoding="utf-8")
    marker = backup.with_name(backup.name + ".metadata.json")
    marker.write_text(
        json.dumps(
            {
                "schema": "vms-backup-v1",
                "destination": str(destination),
                "created_at": "2026-01-01T00:00:00Z",
                "pid": 999,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    original_backup_files = sorted(backup.iterdir())
    with pytest.raises(ConfigurationError, match="unrecovered backup"):
        install_skill(host="kimi", scope="project", project_dir=str(tmp_path))
    # Backup directory and metadata are still present so the user can recover.
    assert backup.is_dir()
    assert marker.is_file()
    assert sorted(backup.iterdir()) == original_backup_files
    # Destination is not created from a refused install.
    assert not destination.exists()


def test_install_leaves_no_backup_after_success(tmp_path: Path) -> None:
    """A successful install must not leave recovery artifacts in the destination's parent."""
    install_skill(host="kimi", scope="project", project_dir=str(tmp_path))
    parent = tmp_path / ".kimi-code" / "skills"
    leftovers = [p for p in parent.iterdir() if ".rollback-" in p.name or p.name.endswith(".rollback.json")]
    assert leftovers == [], f"unexpected leftovers after install: {leftovers}"


def test_install_replaces_existing_destination_atomically(tmp_path: Path) -> None:
    """Reinstalling over an existing install must replace in place without losing SKILL.md."""
    install_skill(host="kimi", scope="project", project_dir=str(tmp_path))
    destination = tmp_path / ".kimi-code" / "skills" / "venice-media"
    second = install_skill(host="kimi", scope="project", project_dir=str(tmp_path))
    assert second["status"] == "installed"
    assert (destination / "SKILL.md").is_file()
