from __future__ import annotations

import httpx
import pytest

from venice_media_skill.client import VeniceClient
from venice_media_skill.errors import (
    ApiError,
    ConfigurationError,
    ConsentRequired,
    DownloadLimitExceeded,
    NetworkSafetyError,
)


def test_json_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer key"
        return httpx.Response(200, json={"data": []})

    with VeniceClient(
        base_url="https://api.example.test/api/v1",
        api_key="key",
        transport=httpx.MockTransport(handler),
        allow_noncanonical_endpoint=True,
    ) as client:
        assert client.get_json("/models") == {"data": []}


def test_binary_response() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\x89PNG\r\n\x1a\n" + b"\x00" * 12, headers={"content-type": "image/png"})

    with VeniceClient(
        base_url="https://api.example.test/api/v1",
        api_key="key",
        transport=httpx.MockTransport(handler),
        allow_noncanonical_endpoint=True,
    ) as client:
        response = client.request("POST", "/image/generate", json_body={})
    assert response.is_binary
    assert response.content is not None and response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_consent_response_raises_typed_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "error": {"code": "needs_consent", "message": "required"},
                "consent_flow": "seedance",
                "consent": {"policy_text": "exact policy"},
            },
        )

    with (
        VeniceClient(
            base_url="https://api.example.test/api/v1",
            api_key="key",
            transport=httpx.MockTransport(handler),
            allow_noncanonical_endpoint=True,
        ) as client,
        pytest.raises(ConsentRequired) as exc_info,
    ):
        client.request("POST", "/video/queue", json_body={})
    assert exc_info.value.payload["consent"]["policy_text"] == "exact policy"


def test_api_error_preserves_status_and_request_id() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={"error": {"message": "provider rejected"}},
            headers={"x-request-id": "req-1"},
        )

    with (
        VeniceClient(
            base_url="https://api.example.test/api/v1",
            api_key="key",
            transport=httpx.MockTransport(handler),
            allow_noncanonical_endpoint=True,
        ) as client,
        pytest.raises(ApiError) as exc_info,
    ):
        client.request("POST", "/video/queue", json_body={})
    assert exc_info.value.status_code == 422
    assert exc_info.value.request_id == "req-1"


def test_redirect_on_authenticated_request_is_rejected() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://api.venice.ai/elsewhere"})

    with (
        VeniceClient(
            base_url="https://api.example.test/api/v1",
            api_key="key",
            transport=httpx.MockTransport(handler),
            allow_noncanonical_endpoint=True,
        ) as client,
        pytest.raises(ApiError) as exc_info,
    ):
        client.request("POST", "/video/queue", json_body={})
    assert exc_info.value.status_code == 302
    assert "redirect" in exc_info.value.message.lower()


def test_httpx_transport_error_is_normalized_to_api_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("simulated read failure")

    with (
        VeniceClient(
            base_url="https://api.example.test/api/v1",
            api_key="key",
            transport=httpx.MockTransport(handler),
            allow_noncanonical_endpoint=True,
        ) as client,
        pytest.raises(ApiError) as exc_info,
    ):
        client.request("POST", "/video/queue", json_body={})
    assert exc_info.value.cause == "ReadError"


def test_constructor_rejects_non_https_base_url() -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        VeniceClient(
            base_url="http://api.venice.ai/api/v1",
            api_key="key",
        )
    assert "HTTPS" in str(exc_info.value) or "https" in str(exc_info.value).lower()


def test_constructor_rejects_non_venice_base_url() -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        VeniceClient(
            base_url="https://attacker.example/api/v1",
            api_key="key",
        )
    assert "host" in str(exc_info.value).lower() or "allow" in str(exc_info.value).lower()


def test_download_public_url_rejects_non_https() -> None:
    with (
        VeniceClient(
            base_url="https://api.example.test/api/v1",
            api_key="key",
            allow_noncanonical_endpoint=True,
        ) as client,
        pytest.raises(NetworkSafetyError) as exc_info,
    ):
        client.download_public_url("http://cdn.venice.ai/clip.mp4")
    assert "https" in str(exc_info.value).lower()


def test_download_public_url_blocks_loopback_before_request() -> None:
    contacts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        contacts.append(request.url.host)
        return httpx.Response(
            302,
            headers={"location": "http://127.0.0.1:80/secret"},
            content=b"",
        )

    transport = httpx.MockTransport(handler)
    with (
        VeniceClient(
            base_url="https://api.example.test/api/v1",
            api_key="key",
            allow_noncanonical_endpoint=True,
        ) as client,
        pytest.raises(NetworkSafetyError) as exc_info,
    ):
        client.download_public_url(
            "https://cdn.venice.ai/clip.mp4",
            transport=transport,
            resolver=lambda _host: ["8.8.8.8"],
        )
    assert contacts == ["cdn.venice.ai"], (
        f"Loopback redirects must be rejected before the second hop is hit, got {contacts}"
    )
    assert "https" in str(exc_info.value).lower() or "host" in str(exc_info.value).lower()


def test_download_public_url_streams_with_size_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = b"\x00\x00\x00\x20ftyp" + b"isom" + b"\x00" * 1024
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": "video/mp4"},
        )

    transport = httpx.MockTransport(handler)
    with (
        VeniceClient(
            base_url="https://api.example.test/api/v1",
            api_key="key",
            allow_noncanonical_endpoint=True,
        ) as client,
        pytest.raises(DownloadLimitExceeded),
    ):
        client.download_public_url(
            "https://cdn.venice.ai/clip.mp4",
            max_bytes=10,
            transport=transport,
            resolver=lambda _host: ["8.8.8.8"],
        )


def test_download_public_url_rejects_content_types_that_fail_validation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"MZ" + b"random-bytes",
            headers={"content-type": "image/png"},
        )

    transport = httpx.MockTransport(handler)
    with (
        VeniceClient(
            base_url="https://api.example.test/api/v1",
            api_key="key",
            allow_noncanonical_endpoint=True,
        ) as client,
        pytest.raises(NetworkSafetyError) as exc_info,
    ):
        client.download_public_url(
            "https://cdn.venice.ai/file.png",
            transport=transport,
            resolver=lambda _host: ["8.8.8.8"],
        )
    msg = str(exc_info.value).lower()
    assert "safety" in msg or "validation" in msg or "content" in msg


def test_constructor_rejects_cdn_as_api_base_url() -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        VeniceClient(base_url="https://cdn.venice.ai/api/v1", api_key="key")
    assert "canonical Venice host" in str(exc_info.value)


def test_constructor_rejects_storage_googleapis_as_api_base_url() -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        VeniceClient(base_url="https://storage.googleapis.com/api/v1", api_key="key")
    assert "canonical Venice host" in str(exc_info.value)


def test_constructor_rejects_r2_cloudflarestorage_as_api_base_url() -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        VeniceClient(base_url="https://r2.cloudflarestorage.com/api/v1", api_key="key")
    assert "canonical Venice host" in str(exc_info.value)


def test_constructor_rejects_arbitrary_venice_subdomain_as_api_base_url() -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        VeniceClient(base_url="https://foo.venice.ai/api/v1", api_key="key")
    assert "canonical Venice host" in str(exc_info.value)
