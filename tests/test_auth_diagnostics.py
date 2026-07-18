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
    assert parser.parse_args(["models", "--type", "image", "--refresh"]).refresh is True
    deprecated = "--refresh-" + "models"
    with pytest.raises(SystemExit):
        parser.parse_args(["models", deprecated])


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


def test_keychain_launcher_scopes_secret_to_exec(monkeypatch: pytest.MonkeyPatch) -> None:
    credential = "test_key_not_real"
    monkeypatch.setattr(keychain.sys, "platform", "darwin")
    monkeypatch.setenv("USER", "tester")
    monkeypatch.delenv("VENICE_API_KEY", raising=False)
    monkeypatch.setattr(keychain.shutil, "which", lambda name: f"/mock/{name}")
    monkeypatch.setattr(
        keychain.subprocess,
        "run",
        lambda *args, **kwargs: keychain.subprocess.CompletedProcess(args[0], 0, stdout=credential + "\n", stderr=""),
    )

    def fake_execve(path: str, argv: list[str], env: dict[str, str]) -> None:
        assert path == "/mock/venice-media"
        assert argv == [path, "doctor", "--online"]
        assert env["VENICE_API_KEY"] == credential
        assert "VENICE_API_KEY" not in os.environ
        raise RuntimeError("exec intercepted")

    monkeypatch.setattr(keychain.os, "execve", fake_execve)
    with pytest.raises(RuntimeError, match="exec intercepted"):
        keychain.main(["doctor", "--online"])


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
