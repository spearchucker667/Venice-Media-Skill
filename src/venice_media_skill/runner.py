"""Operation dispatcher for request manifests."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .client import ApiResponse, VeniceClient
from .errors import OutputError, RequestValidationError
from .jobs import JobStore
from .output import ArtifactWriter, _validate_safe_filename
from .request import MediaRequest
from .util import normalize_media_input, redact_data, timestamp_slug, utc_now_iso

# VMS-017 FIX: Endpoint-specific media size limits (in bytes)
# Based on Venice API documentation and best practices
ENDPOINT_SIZE_LIMITS: dict[str, int] = {
    "image.edit": 25 * 1024 * 1024,      # 25 MiB for image edit
    "image.multi_edit": 25 * 1024 * 1024,  # 25 MiB for multi-edit
    "image.background_remove": 25 * 1024 * 1024,  # 25 MiB for background removal
    "audio.transcribe": 15 * 1024 * 1024,  # 15 MiB for audio transcription
    "video.generate": 500 * 1024 * 1024,  # 500 MiB for video generation
    "audio.generate": 500 * 1024 * 1024,  # 500 MiB for audio generation
    # Default for other operations
    "default": 50 * 1024 * 1024,
}


def _get_size_limit(operation: str) -> int:
    """Get the size limit for a specific operation.
    
    VMS-017 FIX: Return endpoint-specific size limits.
    """
    return ENDPOINT_SIZE_LIMITS.get(operation, ENDPOINT_SIZE_LIMITS["default"])


class MediaRunner:
    def __init__(
        self,
        *,
        client: VeniceClient,
        writer: ArtifactWriter,
        jobs: JobStore,
    ) -> None:
        self.client = client
        self.writer = writer
        self.jobs = jobs

    def run(self, request: MediaRequest) -> dict[str, Any]:
        operation = request.operation
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
            return self._queued_generate(request, media_type="video")
        if operation == "video.retrieve":
            return self._retrieve_existing(request, media_type="video")
        if operation == "audio.tts":
            return self._tts(request)
        if operation == "audio.generate":
            return self._queued_generate(request, media_type="audio")
        if operation == "audio.retrieve":
            return self._retrieve_existing(request, media_type="audio")
        if operation == "audio.transcribe":
            return self._transcribe(request)
        raise RequestValidationError(f"Unsupported operation: {operation}")

    def _image_generate(self, request: MediaRequest) -> dict[str, Any]:
        assert request.model is not None and request.prompt is not None
        payload = {
            "model": request.model,
            "prompt": request.prompt,
            "safe_mode": False,
            "hide_watermark": True,
            "format": "webp",
            "variants": 1,
            **request.parameters,
        }
        # VMS-011 FIX: Don't forcibly overwrite return_binary if user explicitly set it
        # Only set default if not explicitly provided by user
        if "return_binary" not in request.parameters:
            variants = int(payload.get("variants", 1))
            payload["return_binary"] = variants == 1
        if request.execution.dry_run:
            return self._dry_run(request, "/image/generate", payload)
        response = self.client.request("POST", "/image/generate", json_body=payload)
        return self._save(request, response, api_request=payload)

    def _image_edit(self, request: MediaRequest) -> dict[str, Any]:
        assert request.model is not None and request.prompt is not None
        raw_image = request.inputs.get("image")
        if raw_image is None:
            images = request.inputs.get("images")
            raw_image = images[0] if isinstance(images, list) and images else None
        if not isinstance(raw_image, str):
            raise RequestValidationError("image.edit requires a string inputs.image.")
        # VMS-003 FIX: Use modelId instead of model for /image/edit endpoint
        # API documentation shows modelId as the formal field, model as deprecated alias
        # VMS-017 FIX: Use endpoint-specific size limit
        size_limit = _get_size_limit(request.operation)
        payload = {
            "modelId": request.model,
            "prompt": request.prompt,
            "image": normalize_media_input(raw_image, max_bytes=size_limit),
            "safe_mode": False,
            "output_format": "png",
            **request.parameters,
        }
        if request.execution.dry_run:
            return self._dry_run(request, "/image/edit", payload)
        response = self.client.request("POST", "/image/edit", json_body=payload)
        return self._save(request, response, api_request=payload)

    def _image_multi_edit(self, request: MediaRequest) -> dict[str, Any]:
        assert request.model is not None and request.prompt is not None
        images = request.inputs.get("images")
        if not isinstance(images, list) or not all(isinstance(item, str) for item in images):
            raise RequestValidationError(
                "image.multi_edit requires string values in inputs.images."
            )
        # VMS-017 FIX: Use endpoint-specific size limit
        size_limit = _get_size_limit(request.operation)
        payload = {
            "modelId": request.model,
            "prompt": request.prompt,
            "images": [normalize_media_input(item, max_bytes=size_limit) for item in images],
            "safe_mode": False,
            "output_format": "png",
            **request.parameters,
        }
        if request.execution.dry_run:
            return self._dry_run(request, "/image/multi-edit", payload)
        response = self.client.request("POST", "/image/multi-edit", json_body=payload)
        return self._save(request, response, api_request=payload)

    def _image_upscale(self, request: MediaRequest) -> dict[str, Any]:
        image = request.inputs.get("image")
        if not isinstance(image, str):
            raise RequestValidationError("image.upscale requires a string inputs.image.")
        # VMS-004 FIX: Use API-aligned parameter names
        # API expects: scale, enhance, enhanceCreativity, enhancePrompt
        # Not: creativity (undocumented)
        # VMS-017 FIX: Use endpoint-specific size limit
        size_limit = _get_size_limit(request.operation)
        payload = {
            "image": normalize_media_input(image, max_bytes=size_limit),
            "scale": 2,
            "enhance": False,
            **request.parameters,
        }
        # Handle legacy creativity parameter by mapping to enhanceCreativity
        if "creativity" in request.parameters:
            payload["enhance"] = True
            payload["enhanceCreativity"] = request.parameters["creativity"]
            payload.pop("creativity", None)
        if request.execution.dry_run:
            return self._dry_run(request, "/image/upscale", payload)
        response = self.client.request("POST", "/image/upscale", json_body=payload)
        return self._save(request, response, api_request=payload)

    def _image_background_remove(self, request: MediaRequest) -> dict[str, Any]:
        image = request.inputs.get("image")
        if not isinstance(image, str):
            raise RequestValidationError("image.background_remove requires a string inputs.image.")
        # VMS-017 FIX: Use endpoint-specific size limit
        size_limit = _get_size_limit(request.operation)
        normalized = normalize_media_input(image, max_bytes=size_limit)
        payload = (
            {"image_url": normalized}
            if normalized.startswith(("http://", "https://"))
            else {"image": normalized}
        )
        payload.update(request.parameters)
        if request.execution.dry_run:
            return self._dry_run(request, "/image/background-remove", payload)
        response = self.client.request("POST", "/image/background-remove", json_body=payload)
        return self._save(request, response, api_request=payload)

    def _tts(self, request: MediaRequest) -> dict[str, Any]:
        assert request.model is not None and request.prompt is not None
        payload = {
            "model": request.model,
            "input": request.prompt,
            "response_format": "mp3",
            "speed": 1.0,
            "streaming": False,
            **request.parameters,
        }
        if request.execution.dry_run:
            return self._dry_run(request, "/audio/speech", payload)
        response = self.client.request("POST", "/audio/speech", json_body=payload)
        return self._save(request, response, api_request=payload)

    def _transcribe(self, request: MediaRequest) -> dict[str, Any]:
        assert request.model is not None
        audio = request.inputs.get("audio")
        if not isinstance(audio, str):
            raise RequestValidationError("audio.transcribe requires a local path in inputs.audio.")
        path = Path(audio).expanduser().resolve()
        if not path.is_file():
            raise RequestValidationError(f"Audio file does not exist: {path}")
        data = {
            "model": request.model,
            "response_format": str(request.parameters.get("response_format", "json")),
            "timestamps": str(bool(request.parameters.get("timestamps", False))).lower(),
        }
        if request.parameters.get("language"):
            data["language"] = str(request.parameters["language"])
        if request.execution.dry_run:
            return {
                "status": "dry_run",
                "operation": request.operation,
                "endpoint": "/audio/transcriptions",
                "multipart": {"file": str(path), **data},
            }
        with path.open("rb") as handle:
            response = self.client.request(
                "POST",
                "/audio/transcriptions",
                files={"file": (path.name, handle, "application/octet-stream")},
                data=data,
            )
        return self._save_transcript(request, response, api_request={"file": str(path), **data})

    def _queued_generate(self, request: MediaRequest, *, media_type: str) -> dict[str, Any]:
        assert request.model is not None and request.prompt is not None
        payload = self._queue_payload(request, media_type=media_type)
        if request.execution.dry_run:
            return self._dry_run(request, f"/{media_type}/queue", payload)
        if request.execution.quote_first:
            quote_payload = self._quote_payload(request, media_type=media_type)
            quote_response = self.client.request(
                "POST", f"/{media_type}/quote", json_body=quote_payload
            )
            if not request.execution.confirmed_cost:
                return {
                    "status": "approval_required",
                    "operation": request.operation,
                    "quote": quote_response.json_data,
                    "quote_request": redact_data(quote_payload),
                    "next_step": (
                        "Show the quote to the user. After explicit approval, set "
                        "execution.confirmed_cost=true and run the same manifest again."
                    ),
                }
        queued = self.client.request("POST", f"/{media_type}/queue", json_body=payload)
        if not isinstance(queued.json_data, dict):
            raise OutputError(f"/{media_type}/queue returned an unexpected response.")
        queue_id = queued.json_data.get("queue_id")
        if not isinstance(queue_id, str) or not queue_id:
            raise OutputError(f"/{media_type}/queue response did not include queue_id.")
        download_url = queued.json_data.get("download_url")
        self.jobs.create(
            media_type=media_type,
            model=request.model,
            queue_id=queue_id,
            request=request.to_dict(),
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
            model=request.model,
            queue_id=queue_id,
            download_url=download_url if isinstance(download_url, str) else None,
        )

    def _retrieve_existing(self, request: MediaRequest, *, media_type: str) -> dict[str, Any]:
        queue_id = request.parameters.get("queue_id")
        assert isinstance(queue_id, str)
        
        # SECURITY: Reject user-supplied download_url to prevent SSRF
        # download_url should only come from provider responses, not manifests
        if request.parameters.get("download_url"):
            raise RequestValidationError(
                "download_url must not be supplied in manifest. "
                "URLs are obtained from Venice API responses only."
            )
        
        # VMS-018 FIX: Infer model from job store if not provided in manifest
        # This allows retrieval without requiring user to resupply the model
        model = request.model
        download_url = None
        try:
            record = self.jobs.get(queue_id)
        except OutputError:
            # Create new job record if not found
            if model is None:
                raise RequestValidationError(
                    "model is required when creating a new job record. "
                    "Either provide model in manifest or use an existing queue_id."
                )
            self.jobs.create(
                media_type=media_type,
                model=model,
                queue_id=queue_id,
                request=request.to_dict(),
            )
        else:
            # Infer model from job store if not provided
            if model is None:
                model = record.get("model")
                if model is None:
                    raise RequestValidationError(
                        "model is required. Either provide model in manifest "
                        "or ensure the job record contains a model."
                    )
            # Validate that provided model matches job store model
            if model != record.get("model"):
                raise RequestValidationError(
                    f"Provided model '{model}' does not match job store model "
                    f"'{record.get('model')}'. Use the correct model or omit it."
                )
            saved_url = record.get("download_url")
            download_url = saved_url if isinstance(saved_url, str) else None
        
        return self._poll_and_save(
            request,
            media_type=media_type,
            model=model,
            queue_id=queue_id,
            download_url=download_url,
        )

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
            response = self.client.request(
                "POST", f"/{media_type}/retrieve", json_body=retrieve_payload
            )
            if response.is_binary:
                result = self._save(
                    request, response, api_request=retrieve_payload, queue_id=queue_id
                )
                self.jobs.update(queue_id, status="completed", artifact=result.get("artifacts"))
                self._complete_if_requested(request, media_type, model, queue_id)
                return result
            status_payload = response.json_data
            self.jobs.update(queue_id, status="processing", last_response=status_payload)
            status = (
                str(status_payload.get("status", "")).upper()
                if isinstance(status_payload, dict)
                else ""
            )
            if status == "COMPLETED":
                # VMS-006 FIX: Check for download_url in current response, not just original
                # Provider may return URL only at completion or rotate URLs
                current_download_url = download_url
                if isinstance(status_payload, dict):
                    # Check various possible URL fields in the response
                    for url_field in ("download_url", "url"):
                        if isinstance(status_payload.get(url_field), str):
                            current_download_url = status_payload[url_field]
                            break
                    # Also check nested structures
                    if not current_download_url:
                        data = status_payload.get("data")
                        if isinstance(data, dict):
                            for url_field in ("download_url", "url"):
                                if isinstance(data.get(url_field), str):
                                    current_download_url = data[url_field]
                                    break
                if current_download_url:
                    # Update job store with new URL
                    if current_download_url != download_url:
                        self.jobs.update(queue_id, download_url=current_download_url)
                    downloaded = self.client.download_public_url(current_download_url)
                    result = self._save(
                        request,
                        downloaded,
                        api_request=retrieve_payload,
                        queue_id=queue_id,
                    )
                    self.jobs.update(queue_id, status="completed", artifact=result.get("artifacts"))
                    self._complete_if_requested(request, media_type, model, queue_id)
                    return result
                # No download URL available
                self.jobs.update(queue_id, status="completed_without_media")
                raise OutputError(
                    "The queue reported COMPLETED but returned neither binary media nor a "
                    "download_url. Preserve the queue ID and inspect the provider response."
                )
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

    def _complete_if_requested(
        self, request: MediaRequest, media_type: str, model: str, queue_id: str
    ) -> None:
        if request.execution.delete_remote_on_completion:
            self.client.request(
                "POST",
                f"/{media_type}/complete",
                json_body={"model": model, "queue_id": queue_id},
            )
            self.jobs.update(queue_id, remote_media_deleted=True)

    def _queue_payload(self, request: MediaRequest, *, media_type: str) -> dict[str, Any]:
        assert request.model is not None and request.prompt is not None
        payload: dict[str, Any] = {
            "model": request.model,
            "prompt": request.prompt,
            **request.parameters,
        }
        payload.pop("queue_id", None)
        payload.pop("download_url", None)
        if media_type == "video":
            mapping = {
                "image": "image_url",
                "end_image": "end_image_url",
                "audio": "audio_url",
                "video": "video_url",
            }
            for source, target in mapping.items():
                value = request.inputs.get(source)
                if isinstance(value, str):
                    payload[target] = normalize_media_input(value)
            list_mapping = {
                "reference_images": "reference_image_urls",
                "reference_videos": "reference_video_urls",
                "reference_audios": "reference_audio_urls",
                "scene_images": "scene_image_urls",
            }
            for source, target in list_mapping.items():
                values = request.inputs.get(source)
                if isinstance(values, list) and all(isinstance(item, str) for item in values):
                    payload[target] = [normalize_media_input(item) for item in values]
            if request.inputs.get("elements") is not None:
                payload["elements"] = request.inputs["elements"]
            # VMS-005 FIX: Remove automatic consent pre-population
            # Consent must be obtained through proper challenge-response flow
            # The manifest boolean seedance_face_consent is NOT sufficient for consent
            # Instead, the ConsentRequired exception will be raised by the client
            # when the API returns 409 needs_consent, and the user must explicitly
            # approve the exact policy text before resubmitting
            # 
            # This prevents bypassing provider consent requirements with a simple boolean.
            # The proper flow is:
            # 1. Submit without consent -> get 409 with policy_text
            # 2. Show policy_text to user, get explicit confirmation
            # 3. Resubmit same request with consents.seedance object
            # 
            # We intentionally do NOT automatically add consent here.
        return payload

    def _quote_payload(self, request: MediaRequest, *, media_type: str) -> dict[str, Any]:
        assert request.model is not None
        if media_type == "video":
            allowed = {
                "duration",
                "aspect_ratio",
                "resolution",
                "upscale_factor",
                "audio",
                "reference_video_total_duration",
            }
            payload = {"model": request.model}
            payload.update(
                {key: value for key, value in request.parameters.items() if key in allowed}
            )
            video = request.inputs.get("video")
            if isinstance(video, str):
                payload["video_url"] = normalize_media_input(video)
            return payload
        allowed = {"duration_seconds", "character_count"}
        payload = {"model": request.model}
        payload.update({key: value for key, value in request.parameters.items() if key in allowed})
        return payload

    def _save(
        self,
        request: MediaRequest,
        response: ApiResponse,
        *,
        api_request: dict[str, Any],
        queue_id: str | None = None,
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
                "api_request": redact_data(api_request),
            },
        )
        return {
            "status": "completed",
            "operation": request.operation,
            "model": request.model,
            "queue_id": queue_id,
            "artifacts": artifacts,
        }

    def _save_transcript(
        self,
        request: MediaRequest,
        response: ApiResponse,
        *,
        api_request: dict[str, Any],
    ) -> dict[str, Any]:
        directory = (
            Path(request.output.directory).expanduser()
            if request.output.directory
            else self.writer.default_output_dir
        )
        directory.mkdir(parents=True, exist_ok=True)
        
        # NEW: Validate filename safety for transcript output
        extension = ".json" if response.json_data is not None else ".txt"
        filename = request.output.filename or f"audio-transcript-{timestamp_slug()}{extension}"
        
        if request.output.filename:
            _validate_safe_filename(request.output.filename)
        
        # Resolve directory to absolute path
        directory = directory.resolve()
        
        # Construct path and validate containment
        path = directory / filename
        resolved_path = path.resolve()
        if not resolved_path.parent.samefile(directory):
            raise OutputError(
                f"output.filename resolves to {resolved_path} which is outside "
                f"the output directory {directory}"
            )
        path = resolved_path
        
        if path.exists() and not request.output.overwrite:
            path = directory / f"{path.stem}-{timestamp_slug()}{path.suffix}"
        if response.json_data is not None:
            path.write_text(
                json.dumps(response.json_data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        elif response.content is not None:
            path.write_bytes(response.content)
        else:
            raise OutputError("Transcription response was empty.")
        artifact = {
            "path": str(path.resolve()),
            "content_type": response.content_type,
            "bytes": path.stat().st_size,
        }
        if request.output.write_metadata:
            sidecar = path.with_suffix(path.suffix + ".metadata.json")
            sidecar.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "created_at": utc_now_iso(),
                        "operation": request.operation,
                        "model": request.model,
                        "api_request": redact_data(api_request),
                        "artifact": artifact,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            artifact["metadata_path"] = str(sidecar.resolve())
        return {"status": "completed", "operation": request.operation, "artifacts": [artifact]}

    @staticmethod
    def _dry_run(request: MediaRequest, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "dry_run",
            "operation": request.operation,
            "endpoint": endpoint,
            "api_request": redact_data(payload),
        }

    @staticmethod
    def _retrieve_command(media_type: str, model: str, queue_id: str) -> str:
        operation = f"{media_type}.retrieve"
        return (
            "Create a manifest with operation="
            f"{operation!r}, model={model!r}, parameters.queue_id={queue_id!r}, then run it."
        )
