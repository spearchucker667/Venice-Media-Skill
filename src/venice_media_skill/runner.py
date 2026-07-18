"""Operation dispatcher for request manifests.

Each public method on :class:`MediaRunner` accepts a
:class:`~venice_media_skill.request.MediaRequest` and returns a structured
JSON-safe dict. All provider bodies are constructed via
:mod:`venice_media_skill.payloads` so quote, queue, consent, and
retrieve responses are bound to the exact same canonical payload hash.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import payloads
from .client import FILE_MAX_BYTES, ApiResponse, VeniceClient
from .consent import (
    ConsentStore,
    QuoteApprovalStore,
    build_consent_object,
    ensure_seedance_fact,
    quote_cost,
)
from .errors import (
    ConsentApprovalMissing,
    ConsentRequired,
    OutputError,
    QuoteApprovalMismatch,
    QuoteApprovalRequired,
    RequestValidationError,
)
from .jobs import JobStore
from .output import ArtifactWriter
from .request import MediaRequest
from .util import (
    redact_data,
    sha256_file,
    timestamp_slug,
    utc_now_iso,
)

ENDPOINT_SIZE_LIMITS: dict[str, int] = {
    "image.edit": 25 * 1024 * 1024,
    "image.multi_edit": 25 * 1024 * 1024,
    "image.background_remove": 25 * 1024 * 1024,
    "audio.transcribe": 15 * 1024 * 1024,
    "video.generate": 500 * 1024 * 1024,
    "audio.generate": 500 * 1024 * 1024,
    "default": 50 * 1024 * 1024,
}

QUOTE_REQUIRED_OPERATIONS = frozenset({"video.generate", "audio.generate"})


@dataclass(slots=True)
class _ApprovalClaim:
    """Module-level record that tracks claimed approvals.

    Held by the runner across the three-phase queue commit: the quote
    and consent approvals are claimed (moved to a pending section),
    then either finalized on provider acceptance or released on a
    definitive non-charging outcome.
    """

    quote_approval_id: str = ""
    quote_store: Any = None
    consent_challenge_id: str = ""
    consent_store: Any = None


class MediaRunner:
    def __init__(
        self,
        *,
        client: VeniceClient,
        writer: ArtifactWriter,
        jobs: JobStore,
        consent_store: ConsentStore | None = None,
        quote_store: QuoteApprovalStore | None = None,
    ) -> None:
        self.client = client
        self.writer = writer
        self.jobs = jobs
        self.consent_store = consent_store
        self.quote_store = quote_store

    # -- entry points -------------------------------------------------------

    def run(self, request: MediaRequest) -> dict[str, Any]:
        operation = request.operation
        try:
            if operation == "image.generate":
                return self._image_generate(request)
            if operation == "image.edit":
                return self._image_edit(request)
            if operation == "image.multi_edit":
                return self._image_multi_edit(request)
            if operation == "image.upscale":
                return self._image_upscale(request)
            if operation == "image.background_remove":
                return self._image_background_remove(request)
            if operation == "video.generate":
                return self._video_generate(request)
            if operation == "video.retrieve":
                return self._retrieve_existing(request, media_type="video")
            if operation == "audio.tts":
                return self._tts(request)
            if operation == "audio.generate":
                return self._audio_generate(request)
            if operation == "audio.retrieve":
                return self._retrieve_existing(request, media_type="audio")
            if operation == "audio.transcribe":
                return self._transcribe(request)
        except ValueError as exc:
            raise RequestValidationError(str(exc)) from exc
        raise RequestValidationError(f"Unsupported operation: {operation}")

    # -- synchronous image ops ---------------------------------------------

    def _image_generate(self, request: MediaRequest) -> dict[str, Any]:
        canonical = payloads.build_image_generate(request)
        if request.execution.dry_run:
            return self._dry_run(request, canonical)
        response = self._request_preserving_consent(canonical.endpoint, dict(canonical.payload))
        maybe_consent = self._record_consent_if_needed(canonical, response, media_kind="image")
        if maybe_consent:
            return maybe_consent
        return self._save(request, response, canonical=canonical)

    def _image_edit(self, request: MediaRequest) -> dict[str, Any]:
        canonical = payloads.build_image_edit(request)
        if request.execution.dry_run:
            return self._dry_run(request, canonical)
        response = self._request_preserving_consent(canonical.endpoint, dict(canonical.payload))
        maybe_consent = self._record_consent_if_needed(canonical, response, media_kind="image")
        if maybe_consent:
            return maybe_consent
        return self._save(request, response, canonical=canonical)

    def _image_multi_edit(self, request: MediaRequest) -> dict[str, Any]:
        canonical = payloads.build_image_multi_edit(request)
        if request.execution.dry_run:
            return self._dry_run(request, canonical)
        response = self._request_preserving_consent(canonical.endpoint, dict(canonical.payload))
        maybe_consent = self._record_consent_if_needed(canonical, response, media_kind="image")
        if maybe_consent:
            return maybe_consent
        return self._save(request, response, canonical=canonical)

    def _image_upscale(self, request: MediaRequest) -> dict[str, Any]:
        canonical = payloads.build_image_upscale(request)
        if request.execution.dry_run:
            return self._dry_run(request, canonical)
        response = self.client.request("POST", canonical.endpoint, json_body=dict(canonical.payload))
        return self._save(request, response, canonical=canonical)

    def _image_background_remove(self, request: MediaRequest) -> dict[str, Any]:
        canonical = payloads.build_image_background_remove(request)
        if request.execution.dry_run:
            return self._dry_run(request, canonical)
        response = self.client.request("POST", canonical.endpoint, json_body=dict(canonical.payload))
        return self._save(request, response, canonical=canonical)

    def _tts(self, request: MediaRequest) -> dict[str, Any]:
        canonical = payloads.build_tts(request)
        if request.execution.dry_run:
            return self._dry_run(request, canonical, include_inputs=False)
        response = self.client.request("POST", canonical.endpoint, json_body=dict(canonical.payload))
        return self._save(request, response, canonical=canonical)

    # -- transcription ------------------------------------------------------

    def _transcribe(self, request: MediaRequest) -> dict[str, Any]:
        canonical = payloads.build_transcribe(request)
        audio = request.inputs.get("audio")
        if not isinstance(audio, str):
            raise RequestValidationError("audio.transcribe requires a local path in inputs.audio.")
        path = Path(audio).expanduser().resolve()
        if not path.is_file():
            raise RequestValidationError(f"Audio file does not exist: {path}")
        size_limit = ENDPOINT_SIZE_LIMITS["audio.transcribe"]
        actual_size = path.stat().st_size
        if actual_size > size_limit:
            raise RequestValidationError(
                f"audio.transcribe input is {actual_size} bytes; endpoint limit is {size_limit} bytes: {path}"
            )
        data = dict(canonical.payload)
        if request.execution.dry_run:
            return {
                "status": "dry_run",
                "operation": request.operation,
                "endpoint": canonical.endpoint,
                "multipart": {"file": str(path), **data},
            }
        with path.open("rb") as handle:
            response = self.client.request(
                "POST",
                canonical.endpoint,
                files={"file": (path.name, handle, "application/octet-stream")},
                data=data,
            )
        metadata_raw: dict[str, Any] = {
            "file": str(path),
            **{str(key): value for key, value in data.items()},
        }
        metadata_payload = redact_data(metadata_raw)
        metadata_mapping: dict[str, Any] = metadata_payload if isinstance(metadata_payload, dict) else metadata_raw
        return self._save_transcript(request, response, api_request=metadata_mapping)

    # -- paid queued ops ----------------------------------------------------

    def _video_generate(self, request: MediaRequest) -> dict[str, Any]:
        return self._queued_generate(request, media_type="video")

    def _audio_generate(self, request: MediaRequest) -> dict[str, Any]:
        return self._queued_generate(request, media_type="audio")

    def _queued_generate(self, request: MediaRequest, *, media_type: str) -> dict[str, Any]:
        if media_type == "video":
            queue_canonical = payloads.build_video_queue(request)
            quote_canonical = payloads.build_video_quote(request)
        else:
            queue_canonical = payloads.build_audio_queue(request)
            quote_canonical = payloads.build_audio_quote(request)

        # Quote is REQUIRED for paid queued operations. No skip_quote or
        # quote_first=false bypass allowed. The host must explicitly approve
        # the quote via approve-quote before we can queue.
        requires_quote = request.operation in QUOTE_REQUIRED_OPERATIONS
        do_quote = requires_quote

        if request.execution.dry_run:
            return self._dry_run(request, queue_canonical, include_inputs=media_type == "video")

        # Audio quote includes ``character_count`` (billing-only), so the
        # quote and queue wire bodies legitimately differ. Use the quote
        # canonical for approval (billing-relevant) and the queue canonical
        # for the actual submission.
        approval_canonical = quote_canonical

        # Submit exactly one quote request and exactly one queue request.
        # The runner never silently retries paid submissions.
        quote_response: dict[str, Any] = {}
        if do_quote:
            quote_api = self.client.request("POST", quote_canonical.endpoint, json_body=dict(quote_canonical.payload))
            if isinstance(quote_api.json_data, Mapping):
                quote_response = dict(quote_api.json_data)
            else:
                raise OutputError(f"{quote_canonical.endpoint} returned an unexpected response.")

        # Quote approval gate. Without an approval that binds the canonical
        # payload hash, we surface an explicit "next_step" to the host agent
        # rather than queueing. The approval is **claimed** (not consumed)
        # and released on definitive non-charging outcomes or finalized on
        # provider acceptance.
        quote_approval_id: str | None = None
        if do_quote:
            quote_approval_id = self._require_quote_approval(
                request=request,
                canonical=approval_canonical,
                quote_response=quote_response,
            )

        # Seedance consent gate. We only attach consents when a stored
        # approval matches the canonical queue payload hash.
        consent_block, claimed_approvals = self._claim_consent_approval(
            request=request,
            canonical=queue_canonical,
            observed_cost=quote_cost(quote_response),
        )
        if quote_approval_id:
            claimed_approvals.quote_approval_id = quote_approval_id
            claimed_approvals.quote_store = self.quote_store

        queue_body = dict(queue_canonical.payload)
        if consent_block is not None:
            payloads.append_consents(consent_block, queue_body)

        queued = self._request_preserving_consent(queue_canonical.endpoint, queue_body)
        maybe_consent = self._record_consent_if_needed(queue_canonical, queued, media_kind=media_type)
        if maybe_consent:
            # Definitive non-charging outcome: restore claims so the user
            # does not need to re-approve for the unchanged request.
            self._release_claims(claimed_approvals)
            return maybe_consent
        if not isinstance(queued.json_data, dict):
            self._release_claims(claimed_approvals)
            raise OutputError(f"{queue_canonical.endpoint} returned an unexpected response.")
        queue_id = queued.json_data.get("queue_id")
        if not isinstance(queue_id, str) or not queue_id:
            self._release_claims(claimed_approvals)
            raise OutputError(f"{queue_canonical.endpoint} response did not include queue_id.")
        # Provider accepted the paid queue: finalise claims permanently.
        self._finalize_claims(claimed_approvals)
        download_url = queued.json_data.get("download_url")
        self.jobs.create(
            media_type=media_type,
            model=request.model or "",
            queue_id=queue_id,
            request=request.to_dict(),
            input_summary=_summarize_inputs(request),
        )
        if isinstance(download_url, str):
            self.jobs.update(queue_id, download_url=download_url)
        if not request.execution.wait:
            return {
                "status": "queued",
                "operation": request.operation,
                "model": request.model,
                "queue_id": queue_id,
                "download_url_present": isinstance(download_url, str),
                "next_step": self._retrieve_command(media_type, request.model, queue_id),
            }
        return self._poll_and_save(
            request,
            media_type=media_type,
            model=request.model or "",
            queue_id=queue_id,
            download_url=download_url if isinstance(download_url, str) else None,
        )

    def _retrieve_existing(self, request: MediaRequest, *, media_type: str) -> dict[str, Any]:
        if request.parameters.get("download_url"):
            raise RequestValidationError(
                "download_url must not be supplied in manifest; URLs are obtained from Venice API responses only."
            )
        # Canonical queue_id handling lives in ``request.MediaRequest.validate``.
        # ``parameters.queue_id`` is the only accepted source; ``inputs.queue_id``
        # is rejected at the manifest gate.
        queue_id = request.parameters.get("queue_id")
        if not isinstance(queue_id, str) or not queue_id:
            raise RequestValidationError(f"{request.operation} requires a non-empty string parameters.queue_id.")

        model = request.model
        download_url: str | None = None
        try:
            record = self.jobs.get(queue_id)
        except OutputError as exc:
            if model is None:
                raise RequestValidationError(
                    "model is required when creating a brand-new job record. "
                    "Either supply model in the manifest, or use a queue_id from a previous run."
                ) from exc
            self.jobs.create(
                media_type=media_type,
                model=model,
                queue_id=queue_id,
                request=request.to_dict(),
                input_summary=_summarize_inputs(request),
            )
        else:
            if model is None:
                model = record.get("model")
                if model is None:
                    raise RequestValidationError(
                        "model is required. Either provide model in the manifest "
                        "or ensure the job record contains a model."
                    )
            if model != record.get("model"):
                raise RequestValidationError(
                    f"Provided model {model!r} does not match job-store model "
                    f"{record.get('model')!r}; pick a queue_id from the matching run."
                )
            saved = record.get("download_url")
            download_url = saved if isinstance(saved, str) else None

        return self._poll_and_save(
            request,
            media_type=media_type,
            model=model or "",
            queue_id=queue_id,
            download_url=download_url,
        )

    # -- polling and saving -------------------------------------------------

    def _poll_and_save(
        self,
        request: MediaRequest,
        *,
        media_type: str,
        model: str,
        queue_id: str,
        download_url: str | None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + request.execution.timeout_seconds
        retrieve_payload = {
            "model": model,
            "queue_id": queue_id,
            "delete_media_on_completion": False,
        }
        while True:
            response = self.client.request("POST", f"/{media_type}/retrieve", json_body=retrieve_payload)
            if response.is_binary:
                result = self._save_binary(request, response, queue_id=queue_id, api_request=retrieve_payload)
                self.jobs.update(queue_id, status="completed", artifact=result.get("artifacts"))
                self._complete_if_requested(request, media_type, model, queue_id)
                return result
            status_payload = response.json_data
            self.jobs.update(queue_id, status="processing", last_response=status_payload)
            status = str(status_payload.get("status", "")).upper() if isinstance(status_payload, dict) else ""
            if status == "COMPLETED":
                current_download_url = self._fresh_download_url(status_payload, fallback=download_url)
                if current_download_url is None:
                    self.jobs.update(queue_id, status="completed_without_media")
                    raise OutputError(
                        "The queue reported COMPLETED but returned neither binary media nor a "
                        "download_url. Preserve the queue ID and inspect the provider response."
                    )
                if current_download_url != download_url:
                    self.jobs.update(queue_id, download_url=current_download_url)
                # Stream large media directly to disk to avoid buffering in RAM
                staging_dir = (
                    Path(request.output.directory).expanduser()
                    if request.output.directory
                    else self.writer.default_output_dir
                )
                staging_dir.mkdir(parents=True, exist_ok=True)
                destination = staging_dir / f".{media_type}-{queue_id}.download"
                downloaded = self.client.download_public_file(
                    current_download_url,
                    destination=destination,
                    max_bytes=FILE_MAX_BYTES,
                )
                result = self._save_binary(
                    request,
                    downloaded,
                    queue_id=queue_id,
                    api_request=retrieve_payload,
                )
                self.jobs.update(queue_id, status="completed", artifact=result.get("artifacts"))
                self._complete_if_requested(request, media_type, model, queue_id)
                return result
            if status in {"FAILED", "ERROR", "CANCELLED", "CANCELED"}:
                self.jobs.update(queue_id, status=status.lower(), last_response=status_payload)
                return {
                    "status": status.lower(),
                    "operation": request.operation,
                    "queue_id": queue_id,
                    "response": status_payload,
                }
            if not request.execution.wait:
                return {
                    "status": status.lower() or "processing",
                    "operation": request.operation,
                    "queue_id": queue_id,
                    "response": status_payload,
                    "next_step": self._retrieve_command(media_type, model, queue_id),
                }
            if time.monotonic() >= deadline:
                self.jobs.update(queue_id, status="timed_out", last_response=status_payload)
                return {
                    "status": "timed_out",
                    "operation": request.operation,
                    "queue_id": queue_id,
                    "last_response": status_payload,
                    "next_step": self._retrieve_command(media_type, model, queue_id),
                }
            time.sleep(request.execution.poll_interval_seconds)

    @staticmethod
    def _fresh_download_url(payload: Any, *, fallback: str | None) -> str | None:
        candidates: list[Mapping[str, Any]] = []
        if isinstance(payload, Mapping):
            candidates.append(payload)
            data = payload.get("data")
            if isinstance(data, Mapping):
                candidates.append(data)
        for source in candidates:
            for key in ("download_url", "url"):
                value = source.get(key)
                if isinstance(value, str) and value:
                    return value
        return fallback

    def _complete_if_requested(self, request: MediaRequest, media_type: str, model: str, queue_id: str) -> None:
        if request.execution.delete_remote_on_completion:
            self.client.request(
                "POST",
                f"/{media_type}/complete",
                json_body={"model": model, "queue_id": queue_id},
            )
            self.jobs.update(queue_id, remote_media_deleted=True)

    # -- responses ----------------------------------------------------------

    def _save(
        self,
        request: MediaRequest,
        response: ApiResponse,
        *,
        canonical: payloads.CanonicalPayload,
    ) -> dict[str, Any]:
        return self._save_binary_with_canonical(
            request,
            response,
            canonical=canonical,
            api_request=canonical.payload,
        )

    def _save_binary(
        self,
        request: MediaRequest,
        response: ApiResponse,
        *,
        queue_id: str | None,
        api_request: Mapping[str, Any],
    ) -> dict[str, Any]:
        artifacts = self.writer.save_response(
            response,
            operation=request.operation,
            output_dir=request.output.directory,
            filename=request.output.filename,
            overwrite=request.output.overwrite,
            write_metadata=request.output.write_metadata,
            metadata={
                "model": request.model,
                "prompt": request.prompt,
                "queue_id": queue_id,
                "api_request": redact_data(dict(api_request)),
                "payload_hash": _hash_or_none(api_request),
            },
        )
        return {
            "status": "completed",
            "operation": request.operation,
            "model": request.model,
            "queue_id": queue_id,
            "artifacts": artifacts,
        }

    def _save_binary_with_canonical(
        self,
        request: MediaRequest,
        response: ApiResponse,
        *,
        canonical: payloads.CanonicalPayload,
        api_request: Mapping[str, Any],
    ) -> dict[str, Any]:
        artifacts = self.writer.save_response(
            response,
            operation=request.operation,
            output_dir=request.output.directory,
            filename=request.output.filename,
            overwrite=request.output.overwrite,
            write_metadata=request.output.write_metadata,
            metadata={
                "model": request.model,
                "prompt": request.prompt,
                "queue_id": None,
                "api_request": redact_data(_sanitize_api_request(canonical.operation, api_request)),
                "payload_hash": canonical.hash,
                "endpoint": canonical.endpoint,
                "redacted_at": utc_now_iso(),
            },
        )
        return {
            "status": "completed",
            "operation": request.operation,
            "model": request.model,
            "queue_id": None,
            "artifacts": artifacts,
        }

    def _save_transcript(
        self,
        request: MediaRequest,
        response: ApiResponse,
        *,
        api_request: Mapping[str, Any],
    ) -> dict[str, Any]:
        directory = (
            Path(request.output.directory).expanduser() if request.output.directory else self.writer.default_output_dir
        )
        directory = directory.resolve()
        directory.mkdir(parents=True, exist_ok=True)

        extension = ".json" if response.json_data is not None else ".txt"
        filename = request.output.filename or f"audio-transcript-{timestamp_slug()}{extension}"
        if request.output.filename:
            from .output import _validate_safe_filename  # local import

            _validate_safe_filename(request.output.filename)

        candidate = directory / filename
        resolved = candidate.resolve()
        if not resolved.parent.samefile(directory):
            raise OutputError(f"output.filename resolves to {resolved} which is outside {directory}")
        if resolved.exists() and not request.output.overwrite:
            resolved = resolved.with_name(f"{resolved.stem}-{timestamp_slug()}{resolved.suffix}")

        from .output import _atomic_write_bytes, _atomic_write_text  # local

        if response.json_data is not None and response.json_data != [] and response.json_data != {}:
            text = json.dumps(response.json_data, indent=2, ensure_ascii=False) + "\n"
            _atomic_write_text(resolved, text)
        elif response.content is not None:
            _atomic_write_bytes(resolved, response.content)
        else:
            raise OutputError("Transcription response was empty.")
        artifact = {
            "path": str(resolved),
            "content_type": response.content_type,
            "bytes": resolved.stat().st_size,
        }
        if request.output.write_metadata:
            sidecar = resolved.with_suffix(resolved.suffix + ".metadata.json")
            sidecar_payload = {
                "schema_version": 1,
                "created_at": utc_now_iso(),
                "operation": request.operation,
                "model": request.model,
                "api_request": redact_data(dict(api_request)),
                "artifact": artifact,
            }
            _atomic_write_text(sidecar, json.dumps(sidecar_payload, indent=2, sort_keys=True) + "\n")
            artifact["metadata_path"] = str(sidecar.resolve())
        return {"status": "completed", "operation": request.operation, "artifacts": [artifact]}

    # -- dry run + helpers --------------------------------------------------

    @staticmethod
    def _dry_run(
        request: MediaRequest,
        canonical: payloads.CanonicalPayload,
        *,
        include_inputs: bool = True,
    ) -> dict[str, Any]:
        safe_payload = _sanitize_api_request(canonical.operation, canonical.payload)
        out: dict[str, Any] = {
            "status": "dry_run",
            "operation": request.operation,
            "endpoint": canonical.endpoint,
            "payload_hash": canonical.hash,
            "input_hashes": list(canonical.input_hashes),
            "api_request": redact_data(safe_payload),
        }
        if include_inputs:
            out["input_summary"] = _summarize_inputs(request)
        return out

    @staticmethod
    def _retrieve_command(media_type: str, model: str | None, queue_id: str) -> str:
        operation = f"{media_type}.retrieve"
        return (
            "Create a manifest with operation="
            f"{operation!r}, model={model!r}, "
            f"parameters.queue_id={queue_id!r}, then run it."
        )

    # -- consent + quote gates ----------------------------------------------

    def _request_preserving_consent(self, endpoint: str, body: dict[str, Any]) -> ApiResponse:
        """Convert only provider ``409 needs_consent`` into a runner-visible response."""
        try:
            return self.client.request("POST", endpoint, json_body=body)
        except ConsentRequired as exc:
            return ApiResponse(
                status_code=409,
                content_type="application/json",
                headers={},
                json_data=exc.payload,
            )

    def _record_consent_if_needed(
        self,
        canonical: payloads.CanonicalPayload,
        response: ApiResponse,
        *,
        media_kind: str,
    ) -> dict[str, Any] | None:
        if response.status_code != 409 or not isinstance(response.json_data, Mapping):
            return None
        if not ensure_seedance_fact(response.json_data):
            return None
        if self.consent_store is None:
            raise ConsentRequired(payload=dict(response.json_data))
        challenge = self.consent_store.record_challenge(
            operation=canonical.operation,
            model=_redact_model_for_storage(canonical.payload),
            payload_hash=canonical.hash,
            input_hashes=canonical.input_hashes,
            provider_payload=response.json_data,
        )
        return {
            "status": "consent_required",
            "operation": canonical.operation,
            "media_kind": media_kind,
            "challenge_id": challenge.challenge_id,
            "consent_flow": challenge.consent_flow,
            "consent_version": challenge.consent_version,
            "policy_text": challenge.policy_text,
            "face_media_roles": list(challenge.face_media_roles),
            "docs_url": challenge.docs_url,
            "payload_hash": challenge.payload_hash,
            "input_hashes": list(challenge.input_hashes),
            "expires_at": challenge.expires_at,
            "next_step": (
                "Run "
                f"`venice-media approve-consent {challenge.challenge_id} "
                "--acknowledge-policy --max-cost <USD>`. "
                "The command will only persist an approval if the user has read the policy text."
            ),
        }

    def _require_quote_approval(
        self,
        *,
        request: MediaRequest,
        canonical: payloads.CanonicalPayload,
        quote_response: Mapping[str, Any],
    ) -> str | None:
        """Enforce policy: paid queue submissions require a hash-bound approval.

        Returns the claimed approval_id on success, ``None`` if no quote
        is required. The caller must finalize or release the claim after
        the queue call outcome is known.
        """
        cost = quote_cost(quote_response)
        if cost is None:
            raise QuoteApprovalRequired(
                operation=request.operation,
                payload_hash=canonical.hash,
                quote=dict(quote_response),
            )
        if self.quote_store is None:
            raise QuoteApprovalRequired(
                operation=request.operation,
                payload_hash=canonical.hash,
                quote=dict(quote_response),
            )
        approval = self.quote_store.resolve(canonical.hash)
        if approval is None:
            raise QuoteApprovalRequired(
                operation=request.operation,
                payload_hash=canonical.hash,
                quote=dict(quote_response),
            )
        try:
            claimed = self.quote_store.claim(
                approval_id=approval.approval_id,
                current_payload_hash=canonical.hash,
                max_observed_cost=cost,
            )
        except QuoteApprovalMismatch:
            raise
        except ConsentApprovalMissing as exc:
            raise QuoteApprovalRequired(
                operation=request.operation,
                payload_hash=canonical.hash,
                quote=dict(quote_response),
            ) from exc
        return claimed.approval_id

    def _claim_consent_approval(
        self,
        *,
        request: MediaRequest,
        canonical: payloads.CanonicalPayload,
        observed_cost: float | None = None,
    ) -> tuple[dict[str, Any] | None, _ApprovalClaim]:
        """Return a (consent_block, claim) pair.

        The consent approval is **claimed** (moved to a pending section)
        but not yet deleted. The caller must call ``_finalize_claims`` or
        ``_release_claims`` with the returned claim after the queue call
        outcome is known.
        """
        claim = _ApprovalClaim()
        if self.consent_store is None:
            return None, claim
        approval = self.consent_store.claim(canonical.hash, observed_cost=observed_cost)
        if approval is None:
            return None, claim
        claim.consent_challenge_id = approval.challenge_id
        claim.consent_store = self.consent_store
        block = build_consent_object(policy_version="")
        return block, claim

    def _consume_consent_approval(
        self,
        *,
        request: MediaRequest,
        canonical: payloads.CanonicalPayload,
        observed_cost: float | None = None,
    ) -> dict[str, Any] | None:
        """Compatibility wrapper: claim + finalize in a single atomic step.

        Prefer :meth:`_claim_consent_approval` plus the
        ``_finalize_claims`` / ``_release_claims`` three-phase commit in
        new code; this single-step variant exists only for backwards
        compatibility with synchronous / non-queued callers and is
        strictly less recoverable than the three-phase pattern.
        """
        block, claim = self._claim_consent_approval(
            request=request,
            canonical=canonical,
            observed_cost=observed_cost,
        )
        if block is not None:
            self._finalize_claims(claim)
        return block

    def _finalize_claims(self, claim: _ApprovalClaim) -> None:
        """Permanently consume all claimed approvals after provider acceptance."""
        if claim.consent_store and claim.consent_challenge_id:
            claim.consent_store.finalize_claim(challenge_id=claim.consent_challenge_id)
        if claim.quote_store and claim.quote_approval_id:
            claim.quote_store.finalize_claim(approval_id=claim.quote_approval_id)

    def _release_claims(self, claim: _ApprovalClaim) -> None:
        """Restore all claimed approvals on definitive non-charging outcome."""
        if claim.consent_store and claim.consent_challenge_id:
            claim.consent_store.release_claim(challenge_id=claim.consent_challenge_id)
        if claim.quote_store and claim.quote_approval_id:
            claim.quote_store.release_claim(approval_id=claim.quote_approval_id)


# -- module-level helpers -----------------------------------------------------


def _hash_or_none(payload: Mapping[str, Any]) -> str | None:
    try:
        return payloads.sha256_hex(json.dumps(payload, sort_keys=True).encode("utf-8"))
    except TypeError:
        return None


def _sanitize_api_request(operation: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a redacted, size-bounded view of the provider payload for audit logs.

    Strips inline media bytes (data URLs) and signed URL query strings; the
    full body is available via the on-disk sidecar metadata JSON.
    """
    cleaned: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, str) and value.startswith("data:"):
            mime = value[5:].split(";", 1)[0] or "application/octet-stream"
            cleaned[key] = {
                "kind": "local_media",
                "mime_type": mime,
                "redacted": True,
            }
        elif isinstance(value, str) and value.startswith(("http://", "https://")):
            try:
                from urllib.parse import urlparse

                parsed = urlparse(value)
                cleaned[key] = {
                    "host": parsed.hostname,
                    "scheme": parsed.scheme,
                    "path": parsed.path,
                    "redacted_query": True,
                }
            except ValueError:
                cleaned[key] = "[unparseable-url]"
        elif isinstance(value, list):
            cleaned[key] = [_summarize_list_member(item) for item in value]
        else:
            cleaned[key] = value
    cleaned["$bridge_operation"] = operation
    return cleaned


def _summarize_list_member(item: Any) -> Any:
    """Apply string-level redaction to a list item while preserving structure."""
    if isinstance(item, str) and item.startswith("data:"):
        mime = item[5:].split(";", 1)[0] or "application/octet-stream"
        return {
            "kind": "local_media",
            "mime_type": mime,
            "redacted": True,
        }
    if isinstance(item, str) and item.startswith(("http://", "https://")):
        try:
            from urllib.parse import urlparse

            parsed = urlparse(item)
            return {
                "host": parsed.hostname,
                "scheme": parsed.scheme,
                "path": parsed.path,
                "redacted_query": True,
            }
        except ValueError:
            return "[unparseable-url]"
    if isinstance(item, str) and len(item) > 96:
        return item[:64] + "..."
    return item


def _summarize_inputs(request: MediaRequest) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for key, value in request.inputs.items():
        if isinstance(value, str):
            if value.startswith("data:"):
                summary.append({"name": key, "kind": "data_url", "bytes": len(value)})
            elif value.startswith(("http://", "https://")):
                summary.append({"name": key, "kind": "url", "redacted_query": True})
            else:
                path = Path(value).expanduser().resolve()
                if path.is_file() and key in request.inputs:
                    summary.append(
                        {
                            "name": key,
                            "kind": "local_media",
                            "path_hint": path.name,
                            "bytes": path.stat().st_size,
                            "sha256": sha256_file(path),
                        }
                    )
                else:
                    summary.append({"name": key, "kind": "string"})
        elif isinstance(value, list):
            summary.append({"name": key, "kind": "list", "count": len(value)})
        elif isinstance(value, Mapping):
            summary.append({"name": key, "kind": "object"})
        else:
            summary.append({"name": key, "kind": type(value).__name__})
    return summary


def _redact_model_for_storage(payload: Mapping[str, Any]) -> str:
    model = payload.get("model") or payload.get("modelId")
    return str(model) if model is not None else ""


__all__ = [
    "ENDPOINT_SIZE_LIMITS",
    "MediaRunner",
]
