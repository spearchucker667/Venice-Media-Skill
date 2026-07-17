from __future__ import annotations

import json
from pathlib import Path

from venice_media_skill.cli import main


def test_schema_command_emits_json(capsys: object) -> None:
    assert main(["schema"]) == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    payload = json.loads(captured.out)
    assert payload["title"] == "Venice Media Skill request manifest"


def test_validate_openapi_command(capsys: object) -> None:
    path = Path(__file__).resolve().parents[1] / "references" / "venice-openapi.yaml"
    assert main(["validate-openapi", str(path)]) == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    payload = json.loads(captured.out)
    assert payload["status"] == "ok"
    assert payload["missing_required_paths"] == []


def test_dry_run_cli_without_api_key(tmp_path: Path, monkeypatch: object, capsys: object) -> None:
    manifest = tmp_path / "request.json"
    manifest.write_text(
        json.dumps(
            {
                "operation": "image.generate",
                "model": "image-model",
                "prompt": "sunset",
                "execution": {"dry_run": True},
            }
        )
    )
    monkeypatch.delenv("VENICE_API_KEY", raising=False)  # type: ignore[attr-defined]
    monkeypatch.setenv("VENICE_MEDIA_CONFIG_DIR", str(tmp_path / "config"))  # type: ignore[attr-defined]
    monkeypatch.setenv("VENICE_MEDIA_CACHE_DIR", str(tmp_path / "cache"))  # type: ignore[attr-defined]
    monkeypatch.setenv("VENICE_MEDIA_STATE_DIR", str(tmp_path / "state"))  # type: ignore[attr-defined]
    monkeypatch.setenv("VENICE_MEDIA_OUTPUT_DIR", str(tmp_path / "output"))  # type: ignore[attr-defined]
    assert main(["run", str(manifest)]) == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    payload = json.loads(captured.out)
    assert payload["status"] == "dry_run"


def test_model_less_plan_without_api_key(
    tmp_path: Path, monkeypatch: object, capsys: object
) -> None:
    monkeypatch.delenv("VENICE_API_KEY", raising=False)  # type: ignore[attr-defined]
    monkeypatch.setenv("VENICE_MEDIA_CONFIG_DIR", str(tmp_path / "config"))  # type: ignore[attr-defined]
    monkeypatch.setenv("VENICE_MEDIA_CACHE_DIR", str(tmp_path / "cache"))  # type: ignore[attr-defined]
    monkeypatch.setenv("VENICE_MEDIA_STATE_DIR", str(tmp_path / "state"))  # type: ignore[attr-defined]
    monkeypatch.setenv("VENICE_MEDIA_OUTPUT_DIR", str(tmp_path / "output"))  # type: ignore[attr-defined]
    assert main(["plan", "image.upscale"]) == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    payload = json.loads(captured.out)
    assert payload["selected_model"] is None
    assert {item["field"] for item in payload["questions"]} >= {
        "inputs.image",
        "parameters.scale",
    }


def test_install_skill_cli(tmp_path: Path, capsys: object) -> None:
    assert (
        main(
            [
                "install-skill",
                "--host",
                "kimi",
                "--scope",
                "project",
                "--project-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    payload = json.loads(captured.out)
    assert payload["status"] == "installed"
    assert (tmp_path / ".kimi-code" / "skills" / "venice-media" / "SKILL.md").is_file()
