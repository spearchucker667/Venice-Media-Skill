#!/usr/bin/env python3
"""Deterministically refresh the tracked Venice OpenAPI snapshot from upstream.

Usage:
    python scripts/sync-venice-api-docs.py /path/to/api-docs [upstream-sha]

The script:
1. Reads the official swagger.yaml from the provided checkout.
2. Records upstream provenance (repo, commit SHA, UTC timestamp, info.version).
3. Applies deterministic local OpenAPI-validity corrections that do not change
   provider semantics (currently: coerce boolean values to strings inside
   string-typed ``enum`` arrays so YAML 1.1 parsers treat them as strings).
4. Writes the canonical snapshot to ``references/venice-openapi.yaml``.
5. Synchronizes the bundled mirrors under ``adapters/kimi-code/venice-media/``
   and ``src/venice_media_skill/assets/skill/``.
6. Regenerates ``references/request.schema.json`` from the runtime code.
7. Verifies idempotency by refusing if a second run would change the output.

Run from the repository root.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
REFERENCE_DIR = REPO_ROOT / "references"
UPSTREAM_SWAGGER = "swagger.yaml"
PROVENANCE_KEY = "x-venice-media-skill-provenance"
LOCAL_CORRECTIONS = [
    "Coerce boolean values to strings inside string-typed schema fields "
    "(enum/default/example; e.g. enable_web_search) so YAML 1.1 parsers "
    "preserve OpenAPI string semantics; provider contract is unchanged."
]


def _fail(message: str) -> None:
    print(f"sync-venice-api-docs: {message}", file=sys.stderr)
    raise SystemExit(1)


def _run_git(checkout: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=checkout,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        _fail(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _coerce_string_schema_bools(value: Any) -> Any:
    """Recursively coerce booleans inside string-typed schema fields to strings.

    OpenAPI 3.0 requires ``default``/``example``/``enum`` values to match the
    declared schema type. The upstream spec occasionally uses unquoted YAML 1.1
    booleans in string schemas; this correction preserves provider semantics
    while making the file valid.
    """
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        is_string_schema = value.get("type") == "string"
        for k, v in value.items():
            if is_string_schema and k in {"enum", "default", "example"} and isinstance(v, bool):
                result[k] = str(v)
            elif is_string_schema and k == "enum" and isinstance(v, list):
                result[k] = [str(item) if isinstance(item, bool) else item for item in v]
            else:
                result[k] = _coerce_string_schema_bools(v)
        return result
    if isinstance(value, list):
        return [_coerce_string_schema_bools(item) for item in value]
    return value


def _load_upstream(checkout: Path, sha: str | None) -> tuple[dict[str, Any], str]:
    if sha is not None:
        current_sha = _run_git(checkout, "rev-parse", "HEAD")
        if current_sha != sha:
            _fail(f"checkout HEAD {current_sha} does not match requested SHA {sha}")
    else:
        sha = _run_git(checkout, "rev-parse", "HEAD")

    swagger_path = checkout / UPSTREAM_SWAGGER
    if not swagger_path.is_file():
        _fail(f"{swagger_path} not found")

    try:
        payload = yaml.safe_load(swagger_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        _fail(f"unable to parse upstream swagger.yaml: {exc}")

    if not isinstance(payload, dict):
        _fail("upstream swagger.yaml did not parse as a mapping")
    return payload, sha


def _add_provenance(payload: dict[str, Any], upstream_sha: str, retrieved_at: str) -> dict[str, Any]:
    # Remove any legacy provenance keys from previous snapshots.
    payload.pop("x-venice-forge-provenance", None)
    payload.pop(PROVENANCE_KEY, None)
    info_version = str(payload.get("info", {}).get("version", "unknown"))
    payload[PROVENANCE_KEY] = {
        "upstream_repository": "https://github.com/veniceai/api-docs",
        "upstream_commit": upstream_sha,
        "retrieved_utc": retrieved_at,
        "info_version": info_version,
        "local_corrections": LOCAL_CORRECTIONS,
    }
    return payload


def _existing_retrieved_utc(target: Path, upstream_sha: str) -> str | None:
    """Return the previous retrieval timestamp when the upstream SHA is unchanged.

    Keeping the prior timestamp makes a no-op sync against the same upstream
    commit a byte-identical no-op instead of dirtying the tree solely because
    the wall clock advanced.
    """
    if not target.is_file():
        return None
    try:
        payload = yaml.safe_load(target.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return None
    if not isinstance(payload, dict):
        return None
    provenance = payload.get(PROVENANCE_KEY)
    if not isinstance(provenance, dict):
        return None
    if provenance.get("upstream_commit") != upstream_sha:
        return None
    retrieved = provenance.get("retrieved_utc")
    return retrieved if isinstance(retrieved, str) else None


def _dump_yaml(payload: dict[str, Any], path: Path) -> None:
    # default_flow_style=False preserves human readability; sort_keys=False
    # keeps the upstream ordering stable.
    path.write_text(yaml.safe_dump(payload, sort_keys=False, default_flow_style=False), encoding="utf-8")


def _copy_references_to_mirrors() -> None:
    reference_files = [
        "AI Media Generation Reference Manual.md",
        "image_helper.md",
        "venice-openapi.yaml",
        "venice-api-llms.md",
        "seedance-2-0-api-guide.md",
        "seedance-face-consent-api-guide.md",
        "request.schema.json",
    ]
    mirrors = [
        REPO_ROOT / "skills" / "venice-media" / "references",
        REPO_ROOT / "adapters" / "kimi-code" / "venice-media" / "references",
        REPO_ROOT / "src" / "venice_media_skill" / "assets" / "skill" / "references",
    ]
    for mirror in mirrors:
        mirror.mkdir(parents=True, exist_ok=True)
        for rel in reference_files:
            source = REFERENCE_DIR / rel
            destination = mirror / rel
            if source.exists():
                shutil.copy2(source, destination)
            else:
                _fail(f"missing canonical reference: {source}")


def _regenerate_request_schema() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "venice_media_skill",
            "schema",
            "--output",
            str(REFERENCE_DIR / "request.schema.json"),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        _fail(f"schema regeneration failed: {result.stderr.strip()}")


def _validate_openapi(path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "venice_media_skill", "validate-openapi", str(path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        _fail(f"OpenAPI validation failed for {path}: {result.stderr.strip()}")


def _snapshot_is_idempotent(checkout: Path, sha: str, original_output: Path) -> bool:
    """Return True if re-running the sync on the same input reproduces the output.

    ``retrieved_utc`` is preserved from the existing snapshot when the upstream
    SHA is unchanged, so a no-op sync is a true byte-for-byte no-op.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_output = Path(tmpdir) / "venice-openapi.yaml"
        payload, _ = _load_upstream(checkout, sha)
        payload = _coerce_string_schema_bools(payload)
        retrieved_at = _existing_retrieved_utc(original_output, sha) or datetime.now(UTC).isoformat().replace(
            "+00:00", "Z"
        )
        payload = _add_provenance(payload, sha, retrieved_at)
        _dump_yaml(payload, temp_output)
        return temp_output.read_bytes() == original_output.read_bytes()


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh the Venice OpenAPI snapshot from upstream.")
    parser.add_argument("checkout", type=Path, help="Path to a checkout of veniceai/api-docs")
    parser.add_argument("--sha", help="Pinned upstream commit SHA")
    parser.add_argument("--skip-idempotency-check", action="store_true", help="Skip second-run idempotency check")
    args = parser.parse_args()

    checkout = args.checkout.expanduser().resolve()
    if not (checkout / ".git").is_dir():
        _fail(f"{checkout} does not look like a git repository")
    if not (checkout / UPSTREAM_SWAGGER).is_file():
        _fail(f"{checkout / UPSTREAM_SWAGGER} not found")

    payload, upstream_sha = _load_upstream(checkout, args.sha)
    payload = _coerce_string_schema_bools(payload)
    target = REFERENCE_DIR / "venice-openapi.yaml"
    retrieved_at = _existing_retrieved_utc(target, upstream_sha) or datetime.now(UTC).isoformat().replace("+00:00", "Z")
    payload = _add_provenance(payload, upstream_sha, retrieved_at)

    _dump_yaml(payload, target)

    _validate_openapi(target)

    # Regenerate the request schema from runtime code so the two artifacts stay
    # coupled. The mirrors must be updated with both files.
    _regenerate_request_schema()
    _copy_references_to_mirrors()

    if not args.skip_idempotency_check and not _snapshot_is_idempotent(checkout, upstream_sha, target):
        _fail("snapshot is not idempotent; a second run produced different output")

    print(
        f"synced venice-openapi.yaml from veniceai/api-docs@{upstream_sha[:12]} "
        f"(info.version={payload.get('info', {}).get('version')}, retrieved={retrieved_at})"
    )
    print(
        "mirrors updated: skills/venice-media/references, "
        "adapters/kimi-code/venice-media/references, "
        "src/venice_media_skill/assets/skill/references"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
