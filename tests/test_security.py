from __future__ import annotations

import base64
import socket
import unittest.mock
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import ClassVar

import httpx
import pytest

from venice_media_skill.client import Resolver, VeniceClient
from venice_media_skill.consent import ConsentStore, QuoteApprovalStore
from venice_media_skill.errors import (
    ConsentApprovalMissing,
    ContentValidationError,
    DownloadLimitExceeded,
    NetworkSafetyError,
    PayloadValidationError,
    PublicHttpError,
    ReservedParameterError,
)
from venice_media_skill.payloads import RESERVED_PARAMETERS, build_image_generate
from venice_media_skill.request import MediaRequest, request_json_schema

_PNG_BYTES: bytes = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xfa\xff\xff?\x03\x00\x05\xfe\x02\xfe\xa3\x9a\xfa\x05"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _stub_resolver() -> Resolver:
    """Deterministic DNS resolver used by SSRF tests.

    Returns a single globally-routable IP for every host so the network
    safety harness does not depend on real DNS and CI can run offline.
    """

    def _resolve(_host: str) -> Sequence[str]:
        return ["8.8.8.8"]

    return _resolve


def _client(
    transport: httpx.BaseTransport | None = None,
    *,
    resolver: Resolver | None = None,
) -> VeniceClient:
    kwargs: dict[str, object] = {
        "base_url": "https://api.example.test/api/v1",
        "api_key": "key",
        "allow_noncanonical_endpoint": True,
    }
    if transport is not None:
        kwargs["transport"] = transport
    if resolver is not None:
        kwargs["resolver"] = resolver
    return VeniceClient(**kwargs)


def _png_data_url() -> str:
    return "data:image/png;base64," + base64.b64encode(_PNG_BYTES).decode()


# ---------------------------------------------------------------------------
# P0-03 reserved-key rejection
# ---------------------------------------------------------------------------


class TestReservedParameterRejection:
    def test_reserved_keys_known(self) -> None:
        # ``queue_id`` is legitimate on retrieve operations so it stays out
        # of the reserved set; everything else below must be blocked.
        for key in ("model", "prompt", "consents", "download_url", "image_url"):
            assert key in RESERVED_PARAMETERS
        # Sanity: ``queue_id`` is intentionally NOT reserved.
        assert "queue_id" not in RESERVED_PARAMETERS

    def test_parameters_consents_rejected_at_validation(self) -> None:
        manifest = {
            "operation": "video.generate",
            "model": "venice-video",
            "prompt": "hi",
            "parameters": {
                "duration": "5s",
                "consents": {"seedance": {"confirmed_terms_and_privacy": True}},
            },
        }
        with pytest.raises(ReservedParameterError) as exc_info:
            MediaRequest.from_mapping(manifest)
        assert exc_info.value.key == "consents"

    def test_parameters_model_injection_rejected(self) -> None:
        manifest = {
            "operation": "video.generate",
            "model": "cheap",
            "prompt": "original",
            "parameters": {"model": "expensive", "prompt": "changed", "duration": "5s"},
        }
        with pytest.raises(ReservedParameterError):
            MediaRequest.from_mapping(manifest)

    def test_parameters_prompt_injection_rejected(self) -> None:
        manifest = {
            "operation": "video.generate",
            "model": "cheap",
            "prompt": "original",
            "parameters": {"prompt": "changed", "duration": "5s"},
        }
        with pytest.raises(ReservedParameterError):
            MediaRequest.from_mapping(manifest)

    def test_parameters_image_url_injection_rejected(self) -> None:
        manifest = {
            "operation": "video.generate",
            "model": "venice",
            "prompt": "hi",
            "parameters": {"image_url": "http://attacker/secret", "duration": "5s"},
        }
        with pytest.raises(ReservedParameterError) as exc_info:
            MediaRequest.from_mapping(manifest)
        assert exc_info.value.key == "image_url"

    def test_parameters_download_url_injection_rejected(self) -> None:
        manifest = {
            "operation": "video.generate",
            "model": "venice",
            "prompt": "hi",
            "parameters": {"duration": "5s", "download_url": "http://attacker/x"},
        }
        with pytest.raises(ReservedParameterError) as exc_info:
            MediaRequest.from_mapping(manifest)
        assert exc_info.value.key == "download_url"

    def test_image_upscale_rejects_legacy_enhance_via_parameters(self) -> None:
        manifest = {
            "operation": "image.upscale",
            "parameters": {"enhance": True, "enhanceCreativity": 0.01},
            "inputs": {"image": _png_data_url()},
        }
        with pytest.raises(PayloadValidationError):
            MediaRequest.from_mapping(manifest)

    def test_image_generate_rejects_boolean_variants(self) -> None:
        with pytest.raises(PayloadValidationError):
            MediaRequest.from_mapping(
                {
                    "operation": "image.generate",
                    "model": "nano-banana",
                    "prompt": "p",
                    "parameters": {"variants": True},
                }
            )

    def test_image_generate_variants_in_range_is_preserved(self) -> None:
        request = MediaRequest.from_mapping(
            {
                "operation": "image.generate",
                "model": "nano-banana",
                "prompt": "p",
                "parameters": {"variants": 3},
            }
        )
        canonical = build_image_generate(request)
        assert canonical.payload["variants"] == 3
        assert isinstance(canonical.payload["variants"], int)

    def test_unknown_top_level_manifest_field_rejected(self) -> None:
        with pytest.raises(PayloadValidationError):
            MediaRequest.from_mapping({"operation": "image.generate", "model": "m", "prompt": "p", "bogus": True})

    def test_unknown_execution_field_rejected(self) -> None:
        with pytest.raises(PayloadValidationError):
            MediaRequest.from_mapping(
                {
                    "operation": "image.generate",
                    "model": "m",
                    "prompt": "p",
                    "execution": {"dry_run": False, "ironman": True},
                }
            )


# ---------------------------------------------------------------------------
# P0-04 redirect-safe SSRF
# ---------------------------------------------------------------------------


class TestRedirectSafeSSRF:
    def test_reject_http_url(self) -> None:
        client = _client()
        with pytest.raises(NetworkSafetyError, match=r"[Hh]TTPS"):
            client.download_public_url("http://example.com/file")

    def test_reject_loopback_ipv4(self) -> None:
        client = _client()
        with pytest.raises(NetworkSafetyError):
            client.download_public_url("https://127.0.0.1/file")

    def test_reject_loopback_ipv6(self) -> None:
        client = _client()
        with pytest.raises(NetworkSafetyError):
            client.download_public_url("https://[::1]/file")

    def test_reject_loopback_hostname(self) -> None:
        client = _client()
        with pytest.raises(NetworkSafetyError):
            client.download_public_url("https://localhost/file")

    def test_reject_private_ipv4_10(self) -> None:
        client = _client()
        with pytest.raises(NetworkSafetyError):
            client.download_public_url("https://10.0.0.1/file")

    def test_reject_private_ipv4_172(self) -> None:
        client = _client()
        with pytest.raises(NetworkSafetyError):
            client.download_public_url("https://172.16.0.1/file")

    def test_reject_private_ipv4_192(self) -> None:
        client = _client()
        with pytest.raises(NetworkSafetyError):
            client.download_public_url("https://192.168.1.1/file")

    def test_reject_private_ipv6(self) -> None:
        client = _client()
        with pytest.raises(NetworkSafetyError):
            client.download_public_url("https://[fd00::]/file")

    def test_reject_link_local(self) -> None:
        client = _client()
        with pytest.raises(NetworkSafetyError):
            client.download_public_url("https://[fe80::1]/file")

    def test_reject_cloud_metadata_endpoint(self) -> None:
        client = _client()
        with pytest.raises(NetworkSafetyError):
            client.download_public_url("https://169.254.169.254/latest/meta-data")

    def test_redirect_to_loopback_blocked_before_request(self) -> None:
        contacts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            contacts.append(request.url.host)
            return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})

        client = _client(resolver=_stub_resolver())
        with pytest.raises(NetworkSafetyError):
            client.download_public_url("https://cdn.venice.ai/x.png", transport=httpx.MockTransport(handler))
        assert contacts == ["cdn.venice.ai"], (
            f"Loopback redirects must be rejected before the second hop is hit, got {contacts}"
        )

    def test_redirect_target_with_non_https_blocked(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "cdn.venice.ai":
                return httpx.Response(302, headers={"location": "http://cdn.venice.ai/x"})
            return httpx.Response(200, content=_PNG_BYTES, headers={"content-type": "image/png"})

        client = _client(resolver=_stub_resolver())
        with pytest.raises(NetworkSafetyError, match=r"[Hh]TTPS"):
            client.download_public_url("https://cdn.venice.ai/x.png", transport=httpx.MockTransport(handler))

    def test_dns_failure_fails_closed(self) -> None:
        def boom(_host: str) -> Sequence[str]:
            raise socket.gaierror("Name or service not known")

        client = _client(resolver=boom)
        with pytest.raises(NetworkSafetyError) as exc_info:
            client.download_public_url("https://cdn.venice.ai/file")
        assert (
            "dns" in str(exc_info.value).lower()
            or "resolution" in str(exc_info.value).lower()
            or "ip" in str(exc_info.value).lower()
            or "addresses" in str(exc_info.value).lower()
        ), str(exc_info.value)


# ---------------------------------------------------------------------------
# P0-05 streamed, size-bounded downloads
# ---------------------------------------------------------------------------


class TestStreamedDownloadSafety:
    def test_content_length_over_limit_rejected_before_body(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=b"\x00" * 1024,
                headers={"content-type": "image/png", "content-length": "999999999"},
            )

        client = _client(resolver=_stub_resolver())
        with pytest.raises(NetworkSafetyError) as exc_info:
            client.download_public_url(
                "https://cdn.venice.ai/x.png",
                transport=httpx.MockTransport(handler),
                max_bytes=200,
            )
        assert "limit" in str(exc_info.value).lower() or "exceed" in str(exc_info.value).lower()

    def test_incremental_size_check_without_content_length(self) -> None:
        body = _PNG_BYTES + b"\x00" * 4096

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=body, headers={"content-type": "image/png"})

        client = _client(resolver=_stub_resolver())
        with pytest.raises(NetworkSafetyError) as exc_info:
            client.download_public_url(
                "https://cdn.venice.ai/x.png",
                transport=httpx.MockTransport(handler),
                max_bytes=256,
            )
        assert "limit" in str(exc_info.value).lower() or "exceed" in str(exc_info.value).lower()

    def test_chunked_unbounded_stream_rejected(self) -> None:
        """True streaming: the bridge must enforce ``max_bytes`` *while the
        response is in flight*, not after buffering the entire body. We
        mock ``httpx.Client`` to return a streaming response whose
        ``iter_bytes`` would yield forever if the byte budget were not
        applied between chunks.

        After the exception is raised, the captured yield count proves the
        bridge aborted as soon as the first chunk overshot the cap. A
        buffered implementation would have accumulated every chunk into
        ``b"".join(...)`` before raising, so this assertion is what
        distinguishes real streaming from buffered-iter-bytes.
        """

        class StubStreamResponse:
            instances: ClassVar[list[StubStreamResponse]] = []

            def __init__(self) -> None:
                self.status_code = 200
                self.headers = {"content-type": "image/png"}
                self.is_success = True
                self.yielded_bytes: int = 0
                self.yielded_count: int = 0
                StubStreamResponse.instances.append(self)

            def __enter__(self) -> StubStreamResponse:
                return self

            def __exit__(self, *_args: object) -> None:
                return

            def iter_bytes(self, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
                while True:
                    self.yielded_count += 1
                    self.yielded_bytes += chunk_size
                    yield b"\x00" * chunk_size

        class StubHTTPClient:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            def __enter__(self) -> StubHTTPClient:
                return self

            def __exit__(self, *_args: object) -> None:
                return

            def stream(self, method: str, url: str, **_kwargs: object) -> StubStreamResponse:
                return StubStreamResponse()

        StubStreamResponse.instances = []
        with unittest.mock.patch("httpx.Client", StubHTTPClient):
            client = _client(resolver=_stub_resolver())
            with pytest.raises(NetworkSafetyError):
                client.download_public_url(
                    "https://cdn.venice.ai/huge.png",
                    max_bytes=4 * 1024,
                )

        assert StubStreamResponse.instances, "download_public_url never opened a stream context"
        stream = StubStreamResponse.instances[-1]
        assert stream.yielded_count == 1, (
            "streaming bridge must abort after a single chunk exceeds max_bytes; "
            f"got {stream.yielded_count} chunks ({stream.yielded_bytes} bytes)"
        )


# ---------------------------------------------------------------------------
# P0-06 fail-closed magic bytes
# ---------------------------------------------------------------------------


class TestFailClosedMagicBytes:
    def test_executable_bytes_rejected_for_declared_image(self) -> None:
        from venice_media_skill.util import fast_validate_content_type

        with pytest.raises(ContentValidationError):
            fast_validate_content_type(b"MZ" + b"\x00" * 20, "image/png")

    def test_elf_rejected_for_declared_jpeg(self) -> None:
        from venice_media_skill.util import fast_validate_content_type

        with pytest.raises(ContentValidationError):
            fast_validate_content_type(b"\x7fELF" + b"\x00" * 20, "image/jpeg")

    def test_random_bytes_rejected_for_declared_image(self) -> None:
        from venice_media_skill.util import fast_validate_content_type

        with pytest.raises(ContentValidationError):
            fast_validate_content_type(b"random executable bytes", "image/png")

    def test_riff_alone_rejected_for_webp(self) -> None:
        from venice_media_skill.util import fast_validate_content_type

        with pytest.raises(ContentValidationError):
            fast_validate_content_type(b"RIFF\x00\x00\x00\x00AVI " + b"\x00" * 32, "image/webp")

    def test_riff_alone_rejected_for_wav(self) -> None:
        from venice_media_skill.util import fast_validate_content_type

        with pytest.raises(ContentValidationError):
            fast_validate_content_type(b"RIFF\x00\x00\x00\x00AVI " + b"\x00" * 32, "audio/wav")

    def test_valid_png_passes(self) -> None:
        from venice_media_skill.util import fast_validate_content_type

        fast_validate_content_type(_PNG_BYTES, "image/png")

    def test_valid_jpeg_passes(self) -> None:
        from venice_media_skill.util import fast_validate_content_type

        jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 32 + b"\xff\xd9"
        fast_validate_content_type(jpeg_bytes, "image/jpeg")

    def test_valid_webp_passes(self) -> None:
        from venice_media_skill.util import fast_validate_content_type

        webp_bytes = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 32
        fast_validate_content_type(webp_bytes, "image/webp")

    def test_valid_mp4_passes(self) -> None:
        from venice_media_skill.util import fast_validate_content_type

        mp4_bytes = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 32
        fast_validate_content_type(mp4_bytes, "video/mp4")

    def test_validate_content_type_returns_boolean_for_legacy_callers(self) -> None:
        from venice_media_skill.util import validate_content_type

        assert validate_content_type(_PNG_BYTES, "image/png") is True
        assert validate_content_type(b"MZ\x00\x00", "image/png") is False

    def test_undeclared_or_unknown_type_rejected(self) -> None:
        from venice_media_skill.util import fast_validate_content_type

        with pytest.raises(ContentValidationError):
            fast_validate_content_type(_PNG_BYTES, "application/foo+bar")


# ---------------------------------------------------------------------------
# P0-01 consent challenge state machine
# ---------------------------------------------------------------------------


class TestConsentChallengeStateMachine:
    def _seed_challenge(
        self, tmp_path: Path, payload_hash: str, *, store: ConsentStore | None = None
    ) -> tuple[str, ConsentStore]:
        store = store or ConsentStore(tmp_path / "consent_approvals.json")
        challenge = store.record_challenge(
            operation="video.generate",
            model="venice-video",
            payload_hash=payload_hash,
            input_hashes=("" * 64,),
            provider_payload={
                "needs_consent": True,
                "consent_flow": "seedance",
                "consent": {
                    "consent_version": "2024-05-01",
                    "policy_text": "explicit policy",
                },
                "face_media_roles": ["image", "video"],
                "docs_url": "https://docs.example/consent",
            },
        )
        return challenge.challenge_id, store

    def test_consent_challenge_persists_and_is_recoverable(self, tmp_path: Path) -> None:
        cid, store = self._seed_challenge(tmp_path, "h1")
        loaded = store.load_challenge(cid)
        assert loaded is not None
        assert loaded.payload_hash == "h1"

    def test_consent_attach_blocked_until_approval(self, tmp_path: Path) -> None:
        _cid, store = self._seed_challenge(tmp_path, "h2")
        assert store.approval_for("h2") is None

    def test_consent_attach_succeeds_after_approval(self, tmp_path: Path) -> None:
        cid, store = self._seed_challenge(tmp_path, "h3")
        store.approve(
            challenge_id=cid,
            confirmed_max_cost=2.50,
            acknowledge_policy=True,
        )
        approval = store.approval_for("h3")
        assert approval is not None
        assert approval.max_cost == pytest.approx(2.5)

    def test_consent_unacknowledged_policy_rejected(self, tmp_path: Path) -> None:
        cid, store = self._seed_challenge(tmp_path, "h4")
        with pytest.raises(ConsentApprovalMissing):
            store.approve(
                challenge_id=cid,
                confirmed_max_cost=None,
                acknowledge_policy=False,
            )


# ---------------------------------------------------------------------------
# P0-02 quote approval binding
# ---------------------------------------------------------------------------


class TestQuoteApprovalBinding:
    def test_quote_required_for_queued_video(self, tmp_path: Path) -> None:
        store = QuoteApprovalStore(tmp_path / "quote_approvals.json")
        payload_hash = "d" * 64
        approval = store.record(
            operation="video.generate",
            payload_hash=payload_hash,
            quote_response={"quote": 0.5},
            max_cost=1.0,
        )
        consumed = store.consume(
            approval_id=approval.approval_id,
            current_payload_hash=payload_hash,
            max_observed_cost=0.4,
        )
        assert consumed.payload_hash == payload_hash
        with pytest.raises(ConsentApprovalMissing):
            store.consume(
                approval_id=approval.approval_id,
                current_payload_hash=payload_hash,
                max_observed_cost=0.4,
            )

    def test_quote_mismatch_rejected(self, tmp_path: Path) -> None:
        from venice_media_skill.errors import QuoteApprovalMismatch

        store = QuoteApprovalStore(tmp_path / "quote_approvals.json")
        payload_hash = "e" * 64
        approval = store.record(
            operation="video.generate",
            payload_hash=payload_hash,
            quote_response={"quote": 0.5},
            max_cost=1.0,
        )
        with pytest.raises(QuoteApprovalMismatch):
            store.consume(
                approval_id=approval.approval_id,
                current_payload_hash="f" * 64,
                max_observed_cost=0.4,
            )

    def test_quote_max_cost_enforced(self, tmp_path: Path) -> None:
        from venice_media_skill.errors import ConsentApprovalMissing

        store = QuoteApprovalStore(tmp_path / "quote_approvals.json")
        payload_hash = "1" * 64
        approval = store.record(
            operation="video.generate",
            payload_hash=payload_hash,
            quote_response={"quote": 0.5},
            max_cost=1.0,
        )
        with pytest.raises(ConsentApprovalMissing):
            store.consume(
                approval_id=approval.approval_id,
                current_payload_hash=payload_hash,
                max_observed_cost=5.0,
            )


# ---------------------------------------------------------------------------
# P1-01 / P1-02 / P1-03 contract alignment
# ---------------------------------------------------------------------------


class TestContractAlignment:
    def test_edit_payload_uses_model_not_modelid(self) -> None:
        request = MediaRequest.from_mapping(
            {
                "operation": "image.edit",
                "model": "nano-banana",
                "prompt": "p",
                "inputs": {"image": _png_data_url()},
            }
        )
        from venice_media_skill.payloads import build_image_edit

        canonical = build_image_edit(request)
        # The bundled OpenAPI marks ``model`` canonical and ``modelId`` as
        # a deprecated alias. We MUST emit ``model``.
        assert canonical.payload["model"] == "nano-banana"
        assert canonical.payload.get("modelId") in (None, "")

    def test_upscale_payload_uses_creativity_and_scale_only(self) -> None:
        request = MediaRequest.from_mapping(
            {
                "operation": "image.upscale",
                "parameters": {"scale": 4, "creativity": 0.015},
                "inputs": {"image": _png_data_url()},
            }
        )
        from venice_media_skill.payloads import build_image_upscale

        canonical = build_image_upscale(request)
        assert set(canonical.payload) == {"image", "scale", "creativity"}

    def test_edit_payload_hash_matches_quote_hash(self) -> None:
        """Quote and queue payloads for ``video.generate`` must derive from
        the same canonical body so the quote price can be trusted.
        """
        request = MediaRequest.from_mapping(
            {
                "operation": "video.generate",
                "model": "venice-video",
                "prompt": "p",
                "parameters": {"duration": "5s"},
            }
        )
        from venice_media_skill.payloads import build_video_queue, build_video_quote

        assert build_video_queue(request).hash == build_video_quote(request).hash


# ---------------------------------------------------------------------------
# Default policy / allow-list narrowing
# ---------------------------------------------------------------------------


class TestDownloadHostPolicy:
    """``DEFAULT_DOWNLOAD_POLICY`` must reject broad cloud suffixes that
    could otherwise smuggle unsigned/uploaded-to-anyone origins past the
    SSRF allow-list.
    """

    def test_accepts_canonical_venice_cdn(self) -> None:
        from venice_media_skill.client import DEFAULT_DOWNLOAD_POLICY

        assert DEFAULT_DOWNLOAD_POLICY.accepts("cdn.venice.ai") is True

    def test_accepts_venice_operator_suffix(self) -> None:
        from venice_media_skill.client import DEFAULT_DOWNLOAD_POLICY

        assert DEFAULT_DOWNLOAD_POLICY.accepts("media.venice.ai") is True
        assert DEFAULT_DOWNLOAD_POLICY.accepts("api.venice.ai") is True
        assert DEFAULT_DOWNLOAD_POLICY.accepts("streaming.venice.ai") is True

    def test_rejects_broad_cloud_storage_suffixes(self) -> None:
        from venice_media_skill.client import DEFAULT_DOWNLOAD_POLICY

        for host in (
            "attacker.amazonaws.com",
            "s3.amazonaws.com",
            "bucket.cloudflarestorage.com",
            "evil.cloudflarestorage.com",
            "bucket.storage.googleapis.com",
            "anyone.googleapis.com",
        ):
            assert DEFAULT_DOWNLOAD_POLICY.accepts(host) is False, host

    def test_rejects_hosts_unrelated_to_venice(self) -> None:
        from venice_media_skill.client import DEFAULT_DOWNLOAD_POLICY

        for host in ("example.com", "evilexample.ai", "venice.ai.evil.example"):
            assert DEFAULT_DOWNLOAD_POLICY.accepts(host) is False, host

    def test_accepts_is_case_insensitive(self) -> None:
        from venice_media_skill.client import DEFAULT_DOWNLOAD_POLICY

        assert DEFAULT_DOWNLOAD_POLICY.accepts("CDN.VENICE.AI") is True


# ---------------------------------------------------------------------------
# Redirect cycle / relative-location regressions
# ---------------------------------------------------------------------------


class TestRedirectNormalization:
    def test_redirect_cycle_detected_after_default_port_normalization(self) -> None:
        """``https://cdn.venice.ai:443/x`` and ``https://cdn.venice.ai/x``
        must collide in the cycle detector.
        """

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/x.png":
                return httpx.Response(302, headers={"location": "https://cdn.venice.ai:443/x"})
            if request.url.path == "/x":
                return httpx.Response(302, headers={"location": "https://cdn.venice.ai:443/x"})
            return httpx.Response(200, content=_PNG_BYTES, headers={"content-type": "image/png"})

        client = _client(resolver=_stub_resolver())
        with pytest.raises(NetworkSafetyError, match=r"cycle"):
            client.download_public_url("https://cdn.venice.ai/x.png", transport=httpx.MockTransport(handler))

    def test_relative_redirect_resolves_via_urljoin(self) -> None:
        """A 302 to ``/other/file.png`` (relative) must walk to
        ``https://cdn.venice.ai/other/file.png`` and continue.
        """

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/x.png":
                return httpx.Response(302, headers={"location": "/other/file.png"})
            if request.url.path == "/other/file.png":
                return httpx.Response(200, content=_PNG_BYTES, headers={"content-type": "image/png"})
            return httpx.Response(404, content=b"")

        client = _client(resolver=_stub_resolver())
        with httpx.MockTransport(handler) as transport:
            response = client.download_public_url("https://cdn.venice.ai/x.png", transport=transport)
        assert response.is_binary
        assert response.path == "https://cdn.venice.ai/other/file.png"


# ---------------------------------------------------------------------------
# HTTP error status vs. media-validation regression
# ---------------------------------------------------------------------------


class TestPublicHttpError:
    """Public downloads must surface 4xx/5xx as a typed ``PublicHttpError``
    so callers see status / URL / content-type / request-id / body preview
    instead of a misleading magic-byte failure."""

    def test_404_raises_typed_error_with_status(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                404,
                content=b"Not found\n",
                headers={"content-type": "text/plain"},
            )

        client = _client(resolver=_stub_resolver())
        with pytest.raises(PublicHttpError) as exc_info:
            client.download_public_url(
                "https://cdn.venice.ai/missing.png",
                transport=httpx.MockTransport(handler),
            )
        err = exc_info.value
        assert err.status_code == 404
        assert err.url == "https://cdn.venice.ai/missing.png"
        assert err.content_type == "text/plain"
        assert "Not found" in err.body_preview

    def test_500_captures_request_id_and_content_type(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                500,
                content=b"upstream timeout",
                headers={
                    "content-type": "application/json",
                    "x-request-id": "req-xyz",
                },
            )

        client = _client(resolver=_stub_resolver())
        with pytest.raises(PublicHttpError) as exc_info:
            client.download_public_url(
                "https://cdn.venice.ai/x.png",
                transport=httpx.MockTransport(handler),
            )
        err = exc_info.value
        assert err.status_code == 500
        assert err.request_id == "req-xyz"
        assert err.content_type == "application/json"
        assert "upstream timeout" in err.body_preview

    def test_body_preview_is_bounded(self) -> None:
        """At most ``BODY_PREVIEW_LIMIT`` bytes are surfaced, even if the
        server returns an oversized error body. The bridge must NOT
        consume arbitrarily large error bodies during diagnostics."""
        oversized = b"A" * 100_000

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                502,
                content=oversized,
                headers={"content-type": "text/plain"},
            )

        client = _client(resolver=_stub_resolver())
        with pytest.raises(PublicHttpError) as exc_info:
            client.download_public_url(
                "https://cdn.venice.ai/x.png",
                transport=httpx.MockTransport(handler),
            )
        assert len(exc_info.value.body_preview) <= PublicHttpError.BODY_PREVIEW_LIMIT

    def test_403_with_html_body_sanitized_for_text(self) -> None:
        body = b"<html><body>forbidden</body></html>"

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                403,
                content=body,
                headers={"content-type": "text/html"},
            )

        client = _client(resolver=_stub_resolver())
        with pytest.raises(PublicHttpError, match=r"403") as exc:
            client.download_public_url(
                "https://cdn.venice.ai/x.png",
                transport=httpx.MockTransport(handler),
            )
        assert exc.value.status_code == 403
        assert "forbidden" in exc.value.body_preview


# ---------------------------------------------------------------------------
# Schema declaration
# ---------------------------------------------------------------------------


class TestRequestSchemaShape:
    def test_schema_declares_strict_parameters(self) -> None:
        schema = request_json_schema()
        shapes = schema["$defs"]["parameterShapes"]
        assert shapes
        assert all(shape["additionalProperties"] is False for shape in shapes.values())


# ---------------------------------------------------------------------------
# download_public_bytes / download_public_file
# ---------------------------------------------------------------------------


class TestInMemoryBytesDefault:
    def test_default_max_bytes_matches_constant(self) -> None:
        from venice_media_skill.client import IN_MEMORY_MAX_BYTES

        def handler(_request: httpx.Request) -> httpx.Response:
            announced = IN_MEMORY_MAX_BYTES + 1
            return httpx.Response(
                200,
                content=_PNG_BYTES,
                headers={
                    "content-type": "image/png",
                    "content-length": str(announced),
                },
            )

        client = _client(resolver=_stub_resolver())
        with pytest.raises(DownloadLimitExceeded) as exc_info:
            client.download_public_bytes(
                "https://cdn.venice.ai/x.png",
                transport=httpx.MockTransport(handler),
            )
        assert exc_info.value.limit == IN_MEMORY_MAX_BYTES

    def test_explicit_max_bytes_overrides_default(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=_PNG_BYTES + b"\x00" * 4096,
                headers={"content-type": "image/png"},
            )

        client = _client(resolver=_stub_resolver())
        with pytest.raises(DownloadLimitExceeded):
            client.download_public_bytes(
                "https://cdn.venice.ai/x.png",
                max_bytes=128,
                transport=httpx.MockTransport(handler),
            )


class TestFileSink:
    def test_writes_sha256_matches_memory_mode(self, tmp_path: Path) -> None:
        body = _PNG_BYTES + b"\x00" * 1024

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=body, headers={"content-type": "image/png"})

        client = _client(resolver=_stub_resolver())
        transport = httpx.MockTransport(handler)
        with transport:
            memory_response = client.download_public_bytes("https://cdn.venice.ai/x.png", transport=transport)
            destination = tmp_path / "output.png"
            file_response = client.download_public_file(
                "https://cdn.venice.ai/x.png", destination=destination, transport=transport
            )
        assert destination.read_bytes() == body
        assert memory_response.sha256 == file_response.sha256
        assert file_response.is_binary
        assert file_response.content is None
        assert file_response.file_path == destination

    def test_over_cap_leaves_no_partial_tmpfile(self, tmp_path: Path) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=b"\x00" * 4096,
                headers={
                    "content-type": "image/png",
                    "content-length": str(999_999_999),
                },
            )

        client = _client(resolver=_stub_resolver())
        destination = tmp_path / "should-not-exist.png"
        with pytest.raises(DownloadLimitExceeded):
            client.download_public_file(
                "https://cdn.venice.ai/x.png",
                destination=destination,
                max_bytes=128,
                transport=httpx.MockTransport(handler),
            )
        assert not destination.exists()
        leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".venice-media-")]
        assert leftovers == [], f"tmpfile leaked: {leftovers}"

    def test_invalid_content_type_validation_does_not_write_destination(self, tmp_path: Path) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            # Declared as PNG, but magic bytes are a Windows PE executable.
            return httpx.Response(
                200,
                content=b"MZ" + b"\x00" * 64,
                headers={"content-type": "image/png"},
            )

        client = _client(resolver=_stub_resolver())
        destination = tmp_path / "should-not-exist.png"
        with pytest.raises(NetworkSafetyError) as exc_info:
            client.download_public_file(
                "https://cdn.venice.ai/x.png",
                destination=destination,
                transport=httpx.MockTransport(handler),
            )
        assert "content validation" in str(exc_info.value).lower()
        assert not destination.exists()
        leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".venice-media-")]
        assert leftovers == [], f"tmpfile leaked: {leftovers}"

    def test_redirect_in_file_mode_writes_atomically(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/redirect-me":
                return httpx.Response(302, headers={"location": "/other/file.png"})
            if request.url.path == "/other/file.png":
                return httpx.Response(200, content=_PNG_BYTES, headers={"content-type": "image/png"})
            return httpx.Response(404)

        client = _client(resolver=_stub_resolver())
        destination = tmp_path / "redirected.png"
        with httpx.MockTransport(handler) as transport:
            response = client.download_public_file(
                "https://cdn.venice.ai/redirect-me",
                destination=destination,
                transport=transport,
            )
        assert response.path == "https://cdn.venice.ai/other/file.png"
        assert destination.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    def test_4xx_response_does_not_write_destination(self, tmp_path: Path) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, content=b"missing")

        client = _client(resolver=_stub_resolver())
        destination = tmp_path / "should-not-exist.png"
        with pytest.raises(NetworkSafetyError):
            client.download_public_file(
                "https://cdn.venice.ai/x.png",
                destination=destination,
                transport=httpx.MockTransport(handler),
            )
        assert not destination.exists()
        leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".venice-media-")]
        assert leftovers == [], f"tmpfile leaked: {leftovers}"

    def test_existing_destination_is_overwritten(self, tmp_path: Path) -> None:
        existing = tmp_path / "overwrite-me.png"
        existing.write_bytes(b"OLD")
        new_body = _PNG_BYTES

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=new_body, headers={"content-type": "image/png"})

        client = _client(resolver=_stub_resolver())
        with httpx.MockTransport(handler) as transport:
            response = client.download_public_file(
                "https://cdn.venice.ai/x.png",
                destination=existing,
                transport=transport,
            )
        assert response.file_path == existing
        assert existing.read_bytes() == new_body
