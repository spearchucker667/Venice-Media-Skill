from __future__ import annotations

import httpx
import pytest

from venice_media_skill.client import VeniceClient
from venice_media_skill.errors import ApiError, ConsentRequired


def test_json_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer key"
        return httpx.Response(200, json={"data": []})

    with VeniceClient(
        base_url="https://api.example.test/api/v1",
        api_key="key",
        transport=httpx.MockTransport(handler),
    ) as client:
        assert client.get_json("/models") == {"data": []}


def test_binary_response() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"image", headers={"content-type": "image/png"})

    with VeniceClient(
        base_url="https://api.example.test/api/v1",
        api_key="key",
        transport=httpx.MockTransport(handler),
    ) as client:
        response = client.request("POST", "/image/generate", json_body={})
    assert response.content == b"image"
    assert response.is_binary


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
        ) as client,
        pytest.raises(ApiError) as exc_info,
    ):
        client.request("POST", "/video/queue", json_body={})
    assert exc_info.value.status_code == 422
    assert exc_info.value.request_id == "req-1"
