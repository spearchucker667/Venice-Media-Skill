from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from venice_media_skill import cli, keychain
from venice_media_skill.config import Settings
from venice_media_skill.errors import ApiError, TransportError


def _settings(tmp_path: Path, api_key: str | None) -> Settings:
    return Settings(
        base_url="https://api.venice.ai/api/v1",
        api_key=api_key,
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "output",
    )


@pytest.mark.parametrize("credential", ["VENICE_API_KEY_test_key_not_real", "VENICE_ADMIN_KEY_test_key_not_real"])
def test_doctor_treats_prefixes_as_opaque(tmp_path: Path, credential: str) -> None:
    response = httpx.Response(200, json={"data": [{"id": "image-model"}]})
    with patch.object(cli.VeniceClient, "get_json", return_value=response.json()):
        report = cli._doctor(_settings(tmp_path, credential), online=True)
    assert report["status"] == "ok"
    assert report["checks"]["venice_api_key"] == "set"
    assert report["checks"]["online_check"] == {"status": "ok", "image_model_count": 1}
    assert credential not in json.dumps(report)


def test_doctor_malformed_response_is_attention_required(tmp_path: Path) -> None:
    with patch.object(cli.VeniceClient, "get_json", return_value={"data": "wrong"}):
        report = cli._doctor(_settings(tmp_path, "test_key_not_real"), online=True)
    assert report["status"] == "attention_required"
    assert report["checks"]["online_check"]["status"] == "malformed_response"


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (ApiError(401, "test failure"), "rejected_credential"),
        (TransportError("test failure", "ConnectError"), "network_failure"),
    ],
)
def test_doctor_classifies_online_failures_without_secret(tmp_path: Path, failure: Exception, expected: str) -> None:
    credential = "test_key_not_real"
    with patch.object(cli.VeniceClient, "get_json", side_effect=failure):
        report = cli._doctor(_settings(tmp_path, credential), online=True)
    assert report["status"] == "attention_required"
    assert report["checks"]["online_check"]["status"] == expected
    assert credential not in json.dumps(report)


def test_models_refresh_parses_and_deprecated_option_fails() -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["models", "--refresh"]).refresh is True
    assert parser.parse_args(["models", "--type", "image", "--refresh"]).refresh is True
    deprecated = "--refresh-" + "models"
    with pytest.raises(SystemExit):
        parser.parse_args(["models", deprecated])


@pytest.mark.parametrize("command", [[], ["models"], ["doctor"], ["run"]])
def test_cli_help_is_available_for_supported_commands(command: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args([*command, "--help"])
    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "usage:" in output
    if command == ["models"]:
        assert "--refresh" in output
        assert "--type" in output
    if command == ["doctor"]:
        assert "--online" in output


def test_repository_contains_no_deprecated_refresh_option() -> None:
    root = Path(__file__).resolve().parents[1]
    deprecated = "--refresh-" + "models"
    offenders = []
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts or path == Path(__file__):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if deprecated in text:
            offenders.append(str(path.relative_to(root)))
    assert offenders == []


def test_keychain_launcher_scopes_secret_to_exec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    credential = "test_key_not_real"
    launcher = tmp_path / "venice-media-keychain"
    bridge = tmp_path / "venice-media"
    launcher.write_text("launcher", encoding="utf-8")
    bridge.write_text("bridge", encoding="utf-8")
    launcher.chmod(0o700)
    bridge.chmod(0o700)
    monkeypatch.setattr(keychain.sys, "platform", "darwin")
    monkeypatch.setattr(keychain.sys, "argv", [str(launcher)])
    monkeypatch.setenv("USER", "tester")
    monkeypatch.delenv("VENICE_API_KEY", raising=False)
    monkeypatch.setattr(keychain.shutil, "which", lambda name: "/mock/security" if name == "security" else None)
    monkeypatch.setattr(
        keychain.subprocess,
        "run",
        lambda *args, **kwargs: keychain.subprocess.CompletedProcess(args[0], 0, stdout=credential + "\n", stderr=""),
    )

    def fake_execve(path: str, argv: list[str], env: dict[str, str]) -> None:
        assert path == str(bridge)
        assert argv == [path, "doctor", "--online"]
        assert env["VENICE_API_KEY"] == credential
        assert "VENICE_API_KEY" not in os.environ
        raise RuntimeError("exec intercepted")

    monkeypatch.setattr(keychain.os, "execve", fake_execve)
    with pytest.raises(RuntimeError, match="exec intercepted"):
        keychain.main(["doctor", "--online"])


def test_keychain_python_launcher_rejects_recursion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    launcher = tmp_path / "venice-media-keychain"
    launcher.write_text("launcher", encoding="utf-8")
    launcher.chmod(0o700)
    monkeypatch.setenv("VENICE_MEDIA_EXECUTABLE", str(launcher))
    with pytest.raises(ValueError, match="recursive"):
        keychain.resolve_bridge_executable(launcher_path=str(launcher))


def test_keychain_python_launcher_account_falls_back_to_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.delenv("VENICE_KEYCHAIN_ACCOUNT", raising=False)
    monkeypatch.setattr(keychain.shutil, "which", lambda name: "/mock/id" if name == "id" else None)
    monkeypatch.setattr(
        keychain.subprocess,
        "run",
        lambda *args, **kwargs: keychain.subprocess.CompletedProcess(args[0], 0, stdout="fallback-user\n", stderr=""),
    )
    assert keychain._resolve_account() == "fallback-user"


def test_installations_reports_duplicates_without_modification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()
    for directory in (first, second):
        executable = directory / "venice-media"
        executable.write_text("#!/bin/sh\n")
        executable.chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join((str(first), str(second))))
    report = cli._installation_diagnostics()
    assert report["active_executable"] == str(first / "venice-media")
    assert [item["path"] for item in report["installations"]] == [
        str(first / "venice-media"),
        str(second / "venice-media"),
    ]
    assert report["runtime"]["python_interpreter"]
    assert "editable_install" in report["runtime"]


def test_installations_reports_keychain_target_difference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    active_dir = tmp_path / "active"
    keychain_dir = tmp_path / "keychain"
    active_dir.mkdir()
    keychain_dir.mkdir()
    active = active_dir / "venice-media"
    keychain_launcher = keychain_dir / "venice-media-keychain"
    keychain_target = keychain_dir / "venice-media"
    for executable in (active, keychain_launcher, keychain_target):
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join((str(active_dir), str(keychain_dir))))
    report = cli._installation_diagnostics()
    assert report["keychain_launcher"] == {
        "path": str(keychain_launcher),
        "target": str(keychain_target),
        "target_differs_from_active_cli": True,
        "resolution_error": None,
    }
