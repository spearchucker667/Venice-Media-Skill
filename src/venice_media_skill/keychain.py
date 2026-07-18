"""macOS Keychain-backed launcher for sanitized host environments."""

from __future__ import annotations

import os
import shutil

# Fixed argv only; no shell invocation is used.
import subprocess  # nosec B404
import sys
from collections.abc import Sequence
from pathlib import Path

DEFAULT_KEYCHAIN_SERVICE = "venice-api-key"


def _same_executable(left: str, right: str) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return Path(left).expanduser().resolve() == Path(right).expanduser().resolve()


def _launcher_path() -> str:
    invoked = sys.argv[0]
    discovered = shutil.which(invoked) if os.sep not in invoked else None
    return str(Path(discovered or invoked).expanduser().resolve())


def resolve_bridge_executable(*, launcher_path: str | None = None) -> str:
    """Resolve the ordinary CLI without ever selecting this launcher itself."""
    launcher = launcher_path or _launcher_path()
    configured = os.environ.get("VENICE_MEDIA_EXECUTABLE")
    candidates: list[str] = []
    if configured:
        candidates.append(str(Path(configured).expanduser()))

    sibling = str(Path(launcher).with_name("venice-media"))
    if sibling not in candidates:
        candidates.append(sibling)
    discovered = shutil.which("venice-media")
    if discovered and discovered not in candidates:
        candidates.append(discovered)

    for candidate in candidates:
        if not Path(candidate).is_file() or not os.access(candidate, os.X_OK):
            if configured and candidate == candidates[0]:
                raise ValueError("VENICE_MEDIA_EXECUTABLE does not name an executable file.")
            continue
        if _same_executable(candidate, launcher):
            raise ValueError("Refusing recursive Keychain launcher resolution.")
        return str(Path(candidate).resolve())
    raise ValueError("venice-media executable was not found.")


def _resolve_account() -> str:
    configured = os.environ.get("VENICE_KEYCHAIN_ACCOUNT")
    if configured:
        return configured
    user = os.environ.get("USER")
    if user:
        return user
    identity = shutil.which("id")
    if not identity:
        raise ValueError("Unable to determine the Keychain account name.")
    try:
        # The resolved id executable receives one fixed argument and no shell.
        result = subprocess.run(  # nosec B603
            [identity, "-un"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("Unable to determine the Keychain account name.") from exc
    account = result.stdout.strip()
    if not account:
        raise ValueError("Unable to determine the Keychain account name.")
    return account


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if sys.platform != "darwin":
        print("venice-media-keychain is supported only on macOS.", file=sys.stderr)
        return 2
    security = shutil.which("security")
    if not security:
        print("macOS security executable was not found.", file=sys.stderr)
        return 2
    try:
        bridge = resolve_bridge_executable()
        account = _resolve_account()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    service = os.environ.get("VENICE_KEYCHAIN_SERVICE", DEFAULT_KEYCHAIN_SERVICE)
    if not service:
        print("VENICE_KEYCHAIN_SERVICE must not be empty.", file=sys.stderr)
        return 2
    try:
        # The resolved security executable receives structured argv and no shell.
        result = subprocess.run(  # nosec B603
            [security, "find-generic-password", "-a", account, "-s", service, "-w"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        print("Unable to read the configured item from macOS Keychain.", file=sys.stderr)
        return 2
    credential = result.stdout.rstrip("\r\n")
    if not credential:
        print("The configured macOS Keychain item is empty.", file=sys.stderr)
        return 2
    child_env = os.environ.copy()
    child_env["VENICE_API_KEY"] = credential
    # Replacing this process is required to preserve child signals and status.
    os.execve(bridge, [bridge, *args], child_env)  # nosec B606
