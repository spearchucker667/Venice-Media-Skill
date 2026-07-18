"""macOS Keychain-backed launcher for sanitized host environments."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if sys.platform != "darwin":
        print("venice-media-keychain is supported only on macOS.", file=sys.stderr)
        return 2
    security = shutil.which("security")
    bridge = shutil.which("venice-media")
    if not security:
        print("macOS security executable was not found.", file=sys.stderr)
        return 2
    if not bridge:
        print("venice-media executable was not found on PATH.", file=sys.stderr)
        return 2
    account = os.environ.get("USER")
    if not account:
        print("Unable to determine the Keychain account name.", file=sys.stderr)
        return 2
    try:
        result = subprocess.run(
            [security, "find-generic-password", "-a", account, "-s", "venice-api-key", "-w"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        print("Unable to read the venice-api-key item from macOS Keychain.", file=sys.stderr)
        return 2
    credential = result.stdout.rstrip("\r\n")
    if not credential:
        print("The venice-api-key Keychain item is empty.", file=sys.stderr)
        return 2
    child_env = os.environ.copy()
    child_env["VENICE_API_KEY"] = credential
    os.execve(bridge, [bridge, *args], child_env)
