"""Command-line interface consumed by host AI agents."""

from __future__ import annotations

import argparse
import atexit
import importlib.resources as importlib_resources
import importlib.util
import json
import os
import platform
import re
import shutil
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import httpx
import yaml

from . import __version__
from .catalog import ModelCatalog
from .client import VeniceClient
from .config import Settings
from .consent import ConsentStore, QuoteApprovalStore
from .errors import (
    ApiError,
    ConfigurationError,
    ConsentApprovalMissing,
    ConsentApprovalRequired,
    ConsentRequired,
    DurableQueueWriteFailed,
    NetworkSafetyError,
    PayloadValidationError,
    QuoteApprovalMismatch,
    QuoteApprovalRequired,
    RequestValidationError,
    ReservedParameterError,
    TransportError,
    VeniceMediaError,
)
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
    parser.add_argument("--compact", action="store_true", help="Emit compact JSON instead of indented JSON.")
    parser.add_argument(
        "--allow-noncanonical-endpoint",
        action="store_true",
        help="Development only: allow VENICE_BASE_URL to target a noncanonical HTTPS host.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser(
        "doctor", help="Inspect local configuration and optionally test Venice connectivity."
    )
    doctor.add_argument("--online", action="store_true", help="Call GET /models?type=image.")

    models = subparsers.add_parser("models", help="List live Venice models as JSON.")
    models.add_argument("--type", default="all", help="Model type accepted by GET /models.")
    models.add_argument("--refresh", action="store_true", help="Ignore the local one-hour cache.")

    plan = subparsers.add_parser("plan", help="Return model-aware questions for a requested operation.")
    plan.add_argument("operation")
    plan.add_argument("--prompt")
    plan.add_argument("--model")
    plan.add_argument("--refresh", action="store_true", help="Ignore the local one-hour model cache.")

    subparsers.add_parser("installations", help="Report venice-media executables found on PATH without modifying them.")

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
        help="Path to a Venice OpenAPI yaml file. Defaults to the bundled snapshot.",
    )

    approve_consent = subparsers.add_parser(
        "approve-consent",
        help="Approve a persisted Seedance consent challenge bound to a request.",
    )
    approve_consent.add_argument("challenge_id")
    approve_consent.add_argument(
        "--acknowledge-policy",
        action="store_true",
        help="Required: explicitly confirm the user has read the provider policy text.",
    )
    approve_consent.add_argument(
        "--max-cost",
        type=float,
        default=None,
        help="Optional maximum USD cost willing to be charged for this request.",
    )

    approve_quote = subparsers.add_parser(
        "approve-quote",
        help="Record a hash-bound quote approval so the runner may queue the request.",
    )
    approve_quote.add_argument("operation")
    approve_quote.add_argument("payload_hash")
    approve_quote.add_argument("--max-cost", type=float, required=True)
    approve_quote.add_argument(
        "--quote",
        required=True,
        help="Path to a JSON file containing the Venice quote response that the user reviewed.",
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
    except ConsentApprovalRequired as exc:
        _emit(
            {
                "status": "consent_required",
                "error_type": "consent_required",
                "challenge": redact_data(exc.challenge.__dict__),
                "next_step": (
                    f"Run `venice-media approve-consent {exc.challenge.challenge_id}"
                    " --acknowledge-policy --max-cost <USD>` after the user has "
                    "reviewed the policy_text."
                ),
            },
            compact=args.compact,
            stream=sys.stderr,
        )
        return 5
    except QuoteApprovalRequired as exc:
        _emit(
            {
                "status": "quote_approval_required",
                "error_type": "quote_approval_required",
                "operation": exc.operation,
                "payload_hash": exc.payload_hash,
                "quote": redact_data(exc.quote),
                "next_step": (
                    f"Run `venice-media approve-quote {exc.operation} "
                    f"{exc.payload_hash} --quote <path-to-quote.json> "
                    f"--max-cost {exc.quote.get('quote', 'UNKNOWN')}` "
                    f"after the user has reviewed the quote response."
                ),
            },
            compact=args.compact,
            stream=sys.stderr,
        )
        return 6
    except DurableQueueWriteFailed as exc:
        # Venice accepted a paid queue but the local durable record
        # could not be written. The runner does NOT auto-resubmit; the
        # operator must surface ``queue_id`` in their UI and run a
        # retrieve command. Re-approving for the same payload hash on
        # a different manifest would create a SECOND paid submission,
        # so the next_step reminds the operator to recover via queue_id
        # rather than re-approve.
        _emit(
            {
                "status": "error",
                "error_type": "durable_queue_write_failed",
                "operation": exc.operation,
                "model": exc.model,
                "queue_id": exc.queue_id,
                "media_type": exc.media_type,
                "cause": exc.cause,
                "next_step": (
                    f"Venice accepted the paid {exc.operation} request but the local "
                    f"durable record could not be written. Recover by running a "
                    f"manifest with operation={exc.media_type}.retrieve, model={exc.model!r}, "
                    f"parameters.queue_id={exc.queue_id!r}. The runner will NOT "
                    f"auto-resubmit; re-approving the same quote would risk a "
                    f"second paid submission."
                ),
            },
            compact=args.compact,
            stream=sys.stderr,
        )
        return 5
    except ConsentRequired as exc:
        _emit(
            {
                "status": "consent_required",
                "error_type": "consent_required",
                "consent": redact_data(exc.payload),
                "next_step": (
                    "Present the policy text to the user. Only after explicit confirmation "
                    "that they own or have legal consent for every depicted likeness should "
                    "the agent re-submit the same request, at which point the bridge will "
                    "issue a fresh challenge ID via approve-consent."
                ),
            },
            compact=args.compact,
            stream=sys.stderr,
        )
        return 4
    except NetworkSafetyError as exc:
        _emit(
            {
                "status": "error",
                "error_type": "network_safety",
                "url": exc.url,
                "url_sha256": exc.url_sha256,
                "query_redacted": exc.query_redacted,
                "reason": exc.reason,
                "resolved_ip": exc.resolved_ip,
            },
            compact=args.compact,
            stream=sys.stderr,
        )
        return 7
    except QuoteApprovalMismatch as exc:
        _emit(
            {
                "status": "error",
                "error_type": "quote_approval_mismatch",
                "approved_hash": exc.approved_hash,
                "current_hash": exc.current_hash,
            },
            compact=args.compact,
            stream=sys.stderr,
        )
        return 8
    except TransportError as exc:
        _emit(
            {
                "status": "error",
                "error_type": "transport_error",
                "transport": exc.cause,
                "message": exc.message,
            },
            compact=args.compact,
            stream=sys.stderr,
        )
        return 9
    except ApiError as exc:
        _emit(
            {
                "status": "error",
                "error_type": "api_error",
                "status_code": exc.status_code,
                "message": exc.message,
                "request_id": exc.request_id,
                "cause": exc.cause,
                "details": redact_data(exc.payload),
            },
            compact=args.compact,
            stream=sys.stderr,
        )
        return 3
    except httpx.HTTPError as exc:
        _emit(
            {
                "status": "error",
                "error_type": "transport_error",
                "transport": type(exc).__name__,
                "message": str(exc),
            },
            compact=args.compact,
            stream=sys.stderr,
        )
        return 9
    except (VeniceMediaError, PayloadValidationError, ReservedParameterError, ValueError, OSError) as exc:
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
        # Meta-validation so a drifted/recursive schema cannot ship.
        import jsonschema

        try:
            jsonschema.Draft202012Validator.check_schema(payload)
        except jsonschema.SchemaError as exc:
            raise ValueError(f"request schema is not a meta-valid JSON Schema: {exc}") from exc
        if args.output:
            target = Path(args.output).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return {
                "status": "written",
                "path": str(target.resolve()),
                "meta_valid": True,
                "schema_size_bytes": len(json.dumps(payload)),
            }
        return {"status": "ok", "meta_valid": True, "schema": payload}
    if args.command == "validate-openapi":
        path = _resolve_bundled_openapi(args.path)
        return _validate_openapi_dispatch(path)
    if args.command == "installations":
        return _installation_diagnostics()

    settings = Settings.load(require_api_key=False)
    settings.ensure_directories()
    jobs = JobStore(settings.jobs_dir)
    consent_store = ConsentStore(settings.state_dir / "consent_approvals.json")
    quote_store = QuoteApprovalStore(settings.state_dir / "quote_approvals.json")

    if args.command == "approve-consent":
        return _approve_consent(consent_store, args)
    if args.command == "approve-quote":
        return _approve_quote(quote_store, args)
    if args.command == "jobs":
        if args.jobs_command == "list":
            return jobs.list()
        return jobs.get(args.queue_id)
    if args.command == "doctor":
        return _doctor(
            settings,
            online=bool(args.online),
            allow_noncanonical_endpoint=bool(args.allow_noncanonical_endpoint),
        )
    if args.command == "plan" and args.operation in MODELLESS_OPERATIONS:
        return Planner(None).plan(
            args.operation,
            prompt=args.prompt,
            model=args.model,
            refresh_models=bool(args.refresh),
        )
    if args.command == "run":
        request = MediaRequest.from_file(args.manifest)
        if not request.execution.dry_run and not settings.api_key:
            Settings.load(require_api_key=True)
        api_key = settings.api_key or "dry-run-placeholder"
        with VeniceClient(
            base_url=settings.base_url,
            api_key=api_key,
            timeout_seconds=settings.timeout_seconds,
            allow_noncanonical_endpoint=args.allow_noncanonical_endpoint,
        ) as client:
            runner = MediaRunner(
                client=client,
                writer=ArtifactWriter(settings.output_dir),
                jobs=jobs,
                consent_store=consent_store,
                quote_store=quote_store,
            )
            try:
                return runner.run(request)
            except ConsentApprovalRequired as exc:
                # Bail out before queueing so the host agent sees the
                # consent surface and pipes it to the user.
                raise exc
            except QuoteApprovalRequired as exc:
                # Same idea for paid quotes: the host needs to see the
                # quote and present it to the user.
                raise exc
    if not settings.api_key:
        Settings.load(require_api_key=True)
    with VeniceClient(
        base_url=settings.base_url,
        api_key=settings.api_key,
        timeout_seconds=settings.timeout_seconds,
        allow_noncanonical_endpoint=args.allow_noncanonical_endpoint,
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
                refresh_models=bool(args.refresh),
            )
    raise ValueError(f"Unhandled command: {args.command}")


def _approve_consent(store: ConsentStore, args: argparse.Namespace) -> dict[str, Any]:
    if not args.acknowledge_policy:
        raise ConsentApprovalMissing("policy-unacknowledged: --acknowledge-policy is required.")
    approval = store.approve(
        challenge_id=args.challenge_id,
        confirmed_max_cost=args.max_cost,
        acknowledge_policy=True,
    )
    challenge = store.load_challenge(args.challenge_id)
    return {
        "status": "approved",
        "challenge_id": approval.challenge_id,
        "payload_hash": approval.payload_hash,
        "max_cost": approval.max_cost,
        "approved_at": approval.approved_at,
        "expires_at": approval.expires_at,
        "policy_version": challenge.consent_version if challenge else "",
        "policy_text": challenge.policy_text if challenge else "",
    }


def _approve_quote(store: QuoteApprovalStore, args: argparse.Namespace) -> dict[str, Any]:
    quote_path = Path(args.quote).expanduser()
    if not quote_path.is_file():
        raise RequestValidationError(f"Quote file does not exist: {quote_path}")
    try:
        quote_payload = json.loads(
            quote_path.read_text(encoding="utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise RequestValidationError(f"Quote file {quote_path} is not valid JSON: {exc}") from exc
    if not isinstance(quote_payload, dict):
        raise RequestValidationError("Quote file must contain a JSON object.")
    approval = store.record(
        operation=args.operation,
        payload_hash=args.payload_hash,
        quote_response=quote_payload,
        max_cost=float(args.max_cost),
    )
    return {
        "status": "recorded",
        "approval_id": approval.approval_id,
        "operation": approval.operation,
        "payload_hash": approval.payload_hash,
        "max_cost": approval.max_cost,
        "created_at": approval.created_at,
        "expires_at": approval.expires_at,
    }


def _doctor(settings: Settings, *, online: bool, allow_noncanonical_endpoint: bool = False) -> dict[str, Any]:
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
            try:
                with VeniceClient(
                    base_url=settings.base_url,
                    api_key=settings.api_key,
                    timeout_seconds=settings.timeout_seconds,
                    allow_noncanonical_endpoint=allow_noncanonical_endpoint,
                ) as client:
                    payload = client.get_json("/models", params={"type": "image"})
                data = payload.get("data")
                if not isinstance(data, list):
                    checks["online_check"] = {
                        "status": "malformed_response",
                        "message": "Venice returned an unexpected models response shape.",
                    }
                    ok = False
                else:
                    checks["online_check"] = {"status": "ok", "image_model_count": len(data)}
            except ApiError as exc:
                checks["online_check"] = {
                    "status": "rejected_credential" if exc.status_code in {401, 403} else "api_error",
                    "status_code": exc.status_code,
                    "message": (
                        "Venice rejected the credential."
                        if exc.status_code in {401, 403}
                        else "Venice returned an API error."
                    ),
                }
                ok = False
            except (TransportError, httpx.HTTPError) as exc:
                checks["online_check"] = {
                    "status": "network_failure",
                    "transport": type(exc).__name__,
                    "message": "Unable to reach Venice.",
                }
                ok = False
    return {"status": "ok" if ok else "attention_required", "checks": checks}


def _installation_diagnostics() -> dict[str, Any]:
    """Report PATH candidates and the runtime backing the active process."""
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        executable = Path(directory).expanduser() / "venice-media"
        if not executable.is_file() or not os.access(executable, os.X_OK):
            continue
        path = str(executable.absolute())
        if path in seen:
            continue
        seen.add(path)
        resolved = executable.resolve()
        try:
            launcher_text = executable.read_text(encoding="utf-8")[:8192]
        except (OSError, UnicodeDecodeError):
            launcher_text = ""
        match = re.search(r"(?m)^\s*exec\s+[\"']?([^\"'\s]+)", launcher_text)
        wrapper_target = match.group(1) if match else None
        candidates.append(
            {
                "path": path,
                "resolved_target": str(resolved),
                "wrapper_target": wrapper_target,
                "active": path == shutil.which("venice-media"),
            }
        )
    active = shutil.which("venice-media")
    required = ("httpx", "jsonschema", "openapi_spec_validator", "platformdirs", "yaml")
    missing = [name for name in required if importlib.util.find_spec(name) is None]
    package_location = str(Path(__file__).resolve().parent)
    editable = "site-packages" not in package_location and "dist-packages" not in package_location
    return {
        "status": "ok" if not missing else "attention_required",
        "active_executable": active,
        "installations": candidates,
        "runtime": {
            "python_interpreter": sys.executable,
            "package_version": __version__,
            "package_location": package_location,
            "editable_install": editable,
            "missing_runtime_dependencies": missing,
        },
    }


def _validate_openapi(path: Path) -> dict[str, Any]:
    """Return a structured report on the OpenAPI snapshot at ``path``.

    The function returns the report regardless of validity. ``main()``
    inspects ``status`` and raises ``ConfigurationError`` on ``invalid``
    so the CLI surface exits with code 2.
    """
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
    if not missing:
        from openapi_spec_validator import validate
        from openapi_spec_validator.validation.exceptions import OpenAPIValidationError, ValidatorDetectError

        try:
            validate(payload)
        except (OpenAPIValidationError, ValidatorDetectError) as exc:
            raise ConfigurationError(f"OpenAPI specification validation failed: {exc}") from exc
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


def _validate_openapi_dispatch(path: Path) -> dict[str, Any]:
    """``validate-openapi`` CLI subcommand: report OR raise on invalid."""
    result = _validate_openapi(path)
    if result["status"] != "ok":
        missing = ", ".join(result["missing_required_paths"])
        raise ConfigurationError(f"OpenAPI document is missing required paths: {missing}")
    return result


def _emit(payload: Any, *, compact: bool, stream: Any | None = None) -> None:
    if stream is None:
        stream = sys.stdout
    if compact:
        text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    else:
        text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    print(text, file=stream)


def _resolve_bundled_openapi(explicit: str | None) -> Path:
    """Resolve the OpenAPI snapshot path.

    Order:
    1. Caller-supplied path (editable install / CI override).
    2. ``REPO_TOP/references/venice-openapi.yaml`` (development layout).
    3. ``venice_media_skill/assets/skill/references/venice-openapi.yaml``
       packaged as a resource (installed wheel).
    """
    if explicit:
        return Path(explicit)
    repo_path = Path(__file__).resolve().parents[2] / "references" / "venice-openapi.yaml"
    if repo_path.is_file():
        return repo_path
    try:
        package_files = importlib_resources.files("venice_media_skill")
    except ModuleNotFoundError as exc:  # pragma: no cover - safety net
        raise OSError(
            f"OpenAPI snapshot not found via editable layout ({repo_path}) or via the installed package: {exc}"
        ) from exc
    asset = package_files.joinpath("assets", "skill", "references", "venice-openapi.yaml")
    if not asset.is_file():
        raise OSError(f"Bundled OpenAPI snapshot is missing at: {asset}")
    # Some YAML parsers require a real filesystem path. Copy to a temp
    # file owned by the bridge process; the temp path is cleaned up by the
    # OS at process exit.
    target_fd, target_path = tempfile.mkstemp(prefix="venice-openapi-", suffix=".yaml")
    os.close(target_fd)
    Path(target_path).write_bytes(asset.read_bytes())
    atexit.register(lambda: Path(target_path).unlink(missing_ok=True))
    return Path(target_path)


if __name__ == "__main__":
    raise SystemExit(main())
