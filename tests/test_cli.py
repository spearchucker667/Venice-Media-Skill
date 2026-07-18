from __future__ import annotations

import json
from pathlib import Path

from venice_media_skill.cli import main


def test_schema_command_emits_json(capsys: object) -> None:
    assert main(["schema"]) == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    payload = json.loads(captured.out)
    assert payload["status"] == "ok"
    assert payload["meta_valid"] is True
    assert payload["schema"]["title"] == "Venice Media Skill request manifest"


def test_schema_command_writes_to_path(tmp_path: Path, capsys: object) -> None:
    target = tmp_path / "schema.json"
    assert main(["schema", "--output", str(target)]) == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    payload = json.loads(captured.out)
    assert payload["status"] == "written"
    assert payload["meta_valid"] is True
    assert json.loads(target.read_text())["title"] == "Venice Media Skill request manifest"


def test_validate_openapi_command(capsys: object) -> None:
    path = Path(__file__).resolve().parents[1] / "references" / "venice-openapi.yaml"
    assert main(["validate-openapi", str(path)]) == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    payload = json.loads(captured.out)
    assert payload["status"] == "ok"
    assert payload["missing_required_paths"] == []


def test_validate_openapi_missing_paths(tmp_path: Path, capsys: object) -> None:
    """An OpenAPI document missing required paths must surface as exit code 2."""
    import yaml

    openapi = {
        "openapi": "3.1.0",
        "info": {"title": "minimal", "version": "0.0.1"},
        "paths": {},
    }
    src = tmp_path / "openapi.yaml"
    src.write_text(yaml.safe_dump(openapi))
    assert main(["validate-openapi", str(src)]) == 2
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    payload = json.loads(captured.err)
    # Payload on stderr carries the typed error.
    assert payload["status"] == "error"


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


def test_model_less_plan_without_api_key(tmp_path: Path, monkeypatch: object, capsys: object) -> None:
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


def test_allow_noncanonical_endpoint_flag_defaults_to_false() -> None:
    """Regression guard: --allow-noncanonical-endpoint must default to False so a
    future refactor that drops the flag from cli.py fails this test loudly
    rather than silently enabling a non-canonical API endpoint pivot.
    """
    from venice_media_skill.cli import build_parser

    parser = build_parser()
    # Default (no flag) — must be False.
    parsed = parser.parse_args(["doctor"])
    assert parsed.allow_noncanonical_endpoint is False

    # Explicit opt-in — must be True. The flag is registered on the
    # top-level parser, so it must precede the subcommand.
    parsed = parser.parse_args(["--allow-noncanonical-endpoint", "doctor"])
    assert parsed.allow_noncanonical_endpoint is True


def test_allow_noncanonical_endpoint_required_for_off_host_base_url(
    monkeypatch: object, tmp_path: Path, capsys: object
) -> None:
    """Regression guard: ``venice-media run`` rejects a non-canonical VENICE_BASE_URL
    unless the host has explicitly passed ``--allow-noncanonical-endpoint``.
    The bridge must never silently accept an arbitrary host for credentialed calls.
    """
    from venice_media_skill.cli import main

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

    monkeypatch.setenv("VENICE_MEDIA_CONFIG_DIR", str(tmp_path / "config"))  # type: ignore[attr-defined]
    monkeypatch.setenv("VENICE_MEDIA_CACHE_DIR", str(tmp_path / "cache"))  # type: ignore[attr-defined]
    monkeypatch.setenv("VENICE_MEDIA_STATE_DIR", str(tmp_path / "state"))  # type: ignore[attr-defined]
    monkeypatch.setenv("VENICE_MEDIA_OUTPUT_DIR", str(tmp_path / "output"))  # type: ignore[attr-defined]
    monkeypatch.setenv("VENICE_BASE_URL", "https://attacker.example/api/v1")  # type: ignore[attr-defined]
    monkeypatch.setenv("VENICE_API_KEY", "test-key")  # type: ignore[attr-defined]

    # Without the flag: the runner must not silently ship the credential off-host.
    # ``dry_run`` short-circuits the network call, but the VeniceClient
    # construction in ``_dispatch`` still enforces the host gate.
    exit_code = main(["run", str(manifest)])
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert exit_code != 0
    err_payload = json.loads(captured.err)
    assert err_payload["status"] == "error"
    # Either the CLI rejects with exit 2 (ConfigurationError raised before
    # the runner), or it surfaces a typed error mentioning the host.
    err_blob = json.dumps(err_payload).lower()
    assert "canonical" in err_blob or "configuration" in err_blob or "safety" in err_blob
