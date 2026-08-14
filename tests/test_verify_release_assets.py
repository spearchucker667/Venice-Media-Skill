from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path


def _run(bundle: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/verify-release-assets.py", str(bundle)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_verify_release_assets_accepts_matching_bundle(tmp_path: Path) -> None:
    artifact = tmp_path / "package.whl"
    artifact.write_bytes(b"published-wheel")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    (tmp_path / "SHA256SUMS.txt").write_text(f"{digest}  package.whl\n", encoding="utf-8")

    result = _run(tmp_path)

    assert result.returncode == 0
    assert f"verified {digest} package.whl" in result.stdout
    assert "ok (1 artifacts)" in result.stdout


def test_verify_release_assets_rejects_mismatch(tmp_path: Path) -> None:
    (tmp_path / "package.whl").write_bytes(b"tampered")
    (tmp_path / "SHA256SUMS.txt").write_text(f"{'0' * 64}  package.whl\n", encoding="utf-8")

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "checksum mismatch: package.whl" in result.stderr


def test_verify_release_assets_rejects_missing_and_unsafe_paths(tmp_path: Path) -> None:
    (tmp_path / "SHA256SUMS.txt").write_text(f"{'0' * 64}  ../outside.whl\n", encoding="utf-8")
    unsafe = _run(tmp_path)
    assert unsafe.returncode == 1
    assert "unsafe artifact path" in unsafe.stderr

    (tmp_path / "SHA256SUMS.txt").write_text(f"{'0' * 64}  missing.whl\n", encoding="utf-8")
    missing = _run(tmp_path)
    assert missing.returncode == 1
    assert "artifact missing: missing.whl" in missing.stderr
