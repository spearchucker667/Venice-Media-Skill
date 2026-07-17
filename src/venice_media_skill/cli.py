"""Command-line interface consumed by host AI agents."""

from __future__ import annotations

import argparse
import json
import platform
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import yaml

from . import __version__
from .catalog import ModelCatalog
from .client import VeniceClient
from .config import Settings
from .errors import ApiError, ConsentRequired, VeniceMediaError
from .installer import SUPPORTED_HOSTS, SUPPORTED_SCOPES, install_skill
from .jobs import JobStore
from .output import ArtifactWriter
from .planner import MODELLESS_OPERATIONS, Planner
from .request import MediaRequest, request_json_schema
from .runner import MediaRunner
from .util import redact_data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="venice-media",
        description="Agent-friendly Venice API media bridge.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--compact", action="store_true", help="Emit compact JSON instead of indented JSON."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser(
        "doctor", help="Inspect local configuration and optionally test Venice connectivity."
    )
    doctor.add_argument("--online", action="store_true", help="Call GET /models?type=image.")

    models = subparsers.add_parser("models", help="List live Venice models as JSON.")
    models.add_argument("--type", default="all", help="Model type accepted by GET /models.")
    models.add_argument("--refresh", action="store_true", help="Ignore the local one-hour cache.")

    plan = subparsers.add_parser(
        "plan", help="Return model-aware questions for a requested operation."
    )
    plan.add_argument("operation")
    plan.add_argument("--prompt")
    plan.add_argument("--model")
    plan.add_argument("--refresh-models", action="store_true")

    run = subparsers.add_parser("run", help="Execute a JSON request manifest.")
    run.add_argument("manifest")

    install = subparsers.add_parser(
        "install-skill",
        help="Install the bundled Agent Skill into user or project discovery directories.",
    )
    install.add_argument("--host", choices=sorted(SUPPORTED_HOSTS), default="generic")
    install.add_argument("--scope", choices=sorted(SUPPORTED_SCOPES), default="user")
    install.add_argument("--project-dir")

    schema = subparsers.add_parser("schema", help="Print the request-manifest JSON Schema.")
    schema.add_argument("--output", help="Optional output file.")

    validate = subparsers.add_parser(
        "validate-openapi",
        help="Validate that the bundled OpenAPI snapshot parses and contains required media paths.",
    )
    validate.add_argument(
        "path",
        nargs="?",
        default=str(Path(__file__).resolve().parents[2] / "references" / "venice-openapi.yaml"),
    )

    jobs = subparsers.add_parser("jobs", help="Inspect durable local queue records.")
    jobs_sub = jobs.add_subparsers(dest="jobs_command", required=True)
    jobs_sub.add_parser("list", help="List local jobs.")
    job_get = jobs_sub.add_parser("get", help="Read one local job.")
    job_get.add_argument("queue_id")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        payload = _dispatch(args)
    except ConsentRequired as exc:
        _emit(
            {
                "status": "consent_required",
                "error": str(exc),
                "consent": redact_data(exc.payload),
                "next_step": (
                    "Present the exact policy_text to the user. Only after explicit confirmation "
                    "that they own or have legal consent for every depicted likeness, set "
                    "attestations.seedance_face_consent=true and resubmit the same request."
                ),
            },
            compact=args.compact,
            stream=sys.stderr,
        )
        return 4
    except ApiError as exc:
        _emit(
            {
                "status": "error",
                "error_type": "api_error",
                "status_code": exc.status_code,
                "message": exc.message,
                "request_id": exc.request_id,
                "details": redact_data(exc.payload),
            },
            compact=args.compact,
            stream=sys.stderr,
        )
        return 3
    except (VeniceMediaError, ValueError, OSError) as exc:
        _emit(
            {"status": "error", "error_type": type(exc).__name__, "message": str(exc)},
            compact=args.compact,
            stream=sys.stderr,
        )
        return 2
    _emit(payload, compact=args.compact)
    return 0


def _dispatch(args: argparse.Namespace) -> dict[str, Any] | list[Any]:
    if args.command == "install-skill":
        return install_skill(
            host=args.host,
            scope=args.scope,
            project_dir=args.project_dir,
        )
    if args.command == "schema":
        payload = request_json_schema()
        if args.output:
            target = Path(args.output).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            return {"status": "written", "path": str(target.resolve())}
        return payload
    if args.command == "validate-openapi":
        return _validate_openapi(Path(args.path))

    settings = Settings.load(require_api_key=False)
    settings.ensure_directories()
    jobs = JobStore(settings.jobs_dir)
    if args.command == "jobs":
        if args.jobs_command == "list":
            return jobs.list()
        return jobs.get(args.queue_id)
    if args.command == "doctor":
        return _doctor(settings, online=bool(args.online))
    if args.command == "plan" and args.operation in MODELLESS_OPERATIONS:
        return Planner(None).plan(
            args.operation,
            prompt=args.prompt,
            model=args.model,
            refresh_models=bool(args.refresh_models),
        )
    if args.command == "run":
        request = MediaRequest.from_file(args.manifest)
        if not request.execution.dry_run and not settings.api_key:
            Settings.load(require_api_key=True)
        with VeniceClient(
            base_url=settings.base_url,
            api_key=settings.api_key or "dry-run-placeholder",
            timeout_seconds=settings.timeout_seconds,
        ) as client:
            runner = MediaRunner(
                client=client,
                writer=ArtifactWriter(settings.output_dir),
                jobs=jobs,
            )
            return runner.run(request)
    if not settings.api_key:
        Settings.load(require_api_key=True)
    with VeniceClient(
        base_url=settings.base_url,
        api_key=settings.api_key,
        timeout_seconds=settings.timeout_seconds,
    ) as client:
        catalog = ModelCatalog(client, settings.model_cache_file)
        if args.command == "models":
            return {
                "status": "ok",
                "type": args.type,
                "models": catalog.list(args.type, refresh=bool(args.refresh)),
            }
        if args.command == "plan":
            return Planner(catalog).plan(
                args.operation,
                prompt=args.prompt,
                model=args.model,
                refresh_models=bool(args.refresh_models),
            )
    raise ValueError(f"Unhandled command: {args.command}")


def _doctor(settings: Settings, *, online: bool) -> dict[str, Any]:
    checks: dict[str, Any] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "venice_api_key": "set" if settings.api_key else "missing",
        "base_url": settings.base_url,
        "config_dir": str(settings.config_dir),
        "cache_dir": str(settings.cache_dir),
        "state_dir": str(settings.state_dir),
        "output_dir": str(settings.output_dir),
        "online_check": "not_requested",
    }
    ok = bool(settings.api_key)
    if online:
        if not settings.api_key:
            checks["online_check"] = "skipped_missing_api_key"
            ok = False
        else:
            with VeniceClient(
                base_url=settings.base_url,
                api_key=settings.api_key,
                timeout_seconds=settings.timeout_seconds,
            ) as client:
                payload = client.get_json("/models", params={"type": "image"})
                count = len(payload.get("data", [])) if isinstance(payload, dict) else 0
                checks["online_check"] = {"status": "ok", "image_model_count": count}
    return {"status": "ok" if ok else "attention_required", "checks": checks}


def _validate_openapi(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise OSError(f"OpenAPI file does not exist: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("OpenAPI document root must be an object.")
    paths = payload.get("paths")
    if not isinstance(paths, dict):
        raise ValueError("OpenAPI document does not contain a paths object.")
    required = {
        "/models",
        "/image/generate",
        "/image/edit",
        "/image/multi-edit",
        "/image/upscale",
        "/image/background-remove",
        "/audio/speech",
        "/audio/transcriptions",
        "/audio/queue",
        "/audio/retrieve",
        "/audio/quote",
        "/video/queue",
        "/video/retrieve",
        "/video/quote",
    }
    missing = sorted(required.difference(paths))
    info_value = payload.get("info")
    info = cast(dict[str, Any], info_value) if isinstance(info_value, dict) else {}
    return {
        "status": "ok" if not missing else "invalid",
        "path": str(path.resolve()),
        "openapi": payload.get("openapi"),
        "api_version": info.get("version"),
        "path_count": len(paths),
        "missing_required_paths": missing,
    }


def _emit(payload: Any, *, compact: bool, stream: Any | None = None) -> None:
    if stream is None:
        stream = sys.stdout
    if compact:
        text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    else:
        text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    print(text, file=stream)


if __name__ == "__main__":
    raise SystemExit(main())
