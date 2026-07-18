from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "venice-media-keychain"
FAKE_SECRET = "test_venice_key_not_real"


def _write_executable(path: Path, body: str) -> None:
    path.write_text("#!/bin/bash\nset -eu\n" + body, encoding="utf-8")
    path.chmod(0o700)


def _mock_environment(tmp_path: Path, *, user: str | None = "test-user") -> tuple[Path, dict[str, str]]:
    mock_bin = tmp_path / "bin"
    capture = tmp_path / "capture"
    mock_bin.mkdir()
    capture.mkdir()
    shutil.copy2(LAUNCHER, mock_bin / "venice-media-keychain")
    (mock_bin / "venice-media-keychain").chmod(0o700)
    _write_executable(mock_bin / "uname", "printf 'Darwin\\n'\n")
    _write_executable(mock_bin / "id", "printf 'fallback-user\\n'\n")
    _write_executable(
        mock_bin / "security",
        f"printf '%s\\n' \"$@\" > \"$CAPTURE_DIR/security-args\"\nprintf '{FAKE_SECRET}\\n'\n",
    )
    _write_executable(
        mock_bin / "venice-media",
        'printf \'%s\' "${VENICE_API_KEY-}" > "$CAPTURE_DIR/key"\n'
        'printf \'%s\\n\' "$@" > "$CAPTURE_DIR/args"\n'
        'exit "${CHILD_EXIT_CODE:-0}"\n',
    )
    env = {
        "HOME": str(tmp_path / "home"),
        "PATH": str(mock_bin),
        "CAPTURE_DIR": str(capture),
    }
    if user is not None:
        env["USER"] = user
    return mock_bin, env


def test_shell_launcher_sanitized_environment_scopes_secret_and_preserves_exit(tmp_path: Path) -> None:
    mock_bin, env = _mock_environment(tmp_path)
    env["CHILD_EXIT_CODE"] = "23"
    result = subprocess.run(
        ["/bin/bash", str(mock_bin / "venice-media-keychain"), "doctor", "--online"],
        env=env,
        capture_output=True,
        text=True,
    )
    capture = Path(env["CAPTURE_DIR"])
    assert result.returncode == 23
    assert capture.joinpath("key").read_text() == FAKE_SECRET
    assert capture.joinpath("args").read_text().splitlines() == ["doctor", "--online"]
    assert capture.joinpath("security-args").read_text().splitlines() == [
        "find-generic-password",
        "-a",
        "test-user",
        "-s",
        "venice-api-key",
        "-w",
    ]
    assert FAKE_SECRET not in result.stdout + result.stderr


def test_shell_launcher_honors_account_service_and_executable_overrides(tmp_path: Path) -> None:
    mock_bin, env = _mock_environment(tmp_path)
    override = tmp_path / "custom-bridge"
    _write_executable(override, "printf 'override' > \"$CAPTURE_DIR/target\"\n")
    env.update(
        {
            "VENICE_MEDIA_EXECUTABLE": str(override),
            "VENICE_KEYCHAIN_ACCOUNT": "account-override",
            "VENICE_KEYCHAIN_SERVICE": "service-override",
        }
    )
    result = subprocess.run(
        ["/bin/bash", str(mock_bin / "venice-media-keychain")],
        env=env,
        capture_output=True,
        text=True,
    )
    capture = Path(env["CAPTURE_DIR"])
    assert result.returncode == 0
    assert capture.joinpath("target").read_text() == "override"
    assert capture.joinpath("security-args").read_text().splitlines()[2:6] == [
        "account-override",
        "-s",
        "service-override",
        "-w",
    ]


def test_shell_launcher_uses_id_fallback(tmp_path: Path) -> None:
    mock_bin, env = _mock_environment(tmp_path, user=None)
    result = subprocess.run(
        ["/bin/bash", str(mock_bin / "venice-media-keychain")],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    security_args = Path(env["CAPTURE_DIR"]).joinpath("security-args").read_text().splitlines()
    assert security_args[2] == "fallback-user"


def test_shell_launcher_rejects_recursive_target_without_reading_keychain(tmp_path: Path) -> None:
    mock_bin, env = _mock_environment(tmp_path)
    launcher = mock_bin / "venice-media-keychain"
    env["VENICE_MEDIA_EXECUTABLE"] = str(launcher)
    result = subprocess.run(["/bin/bash", str(launcher)], env=env, capture_output=True, text=True)
    assert result.returncode == 2
    assert "recursive" in result.stderr.lower()
    assert not Path(env["CAPTURE_DIR"]).joinpath("security-args").exists()
    assert FAKE_SECRET not in result.stdout + result.stderr


def test_shell_launcher_has_restrictive_install_contract() -> None:
    install = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    uninstall = (ROOT / "scripts" / "uninstall.sh").read_text(encoding="utf-8")
    assert 'chmod 0700 "$KEYCHAIN_STAGING"' in install
    assert '"$BIN_HOME/venice-media-keychain"' in uninstall
    assert LAUNCHER.stat().st_mode & 0o777 == 0o755


@pytest.mark.skipif(os.name == "nt", reason="bash launcher is POSIX-only")
def test_shell_launcher_syntax() -> None:
    assert subprocess.run(["/bin/bash", "-n", str(LAUNCHER)], check=False).returncode == 0
