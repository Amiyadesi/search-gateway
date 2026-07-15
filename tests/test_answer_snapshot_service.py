import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.schemas.evidence import AnswerModelsRequest, AnswerSnapshotRequest
from app.services.answer_snapshot_service import AnswerSnapshotService
from app.utils.errors import GatewayError


class FakeResponse:
    def __init__(self, data, status=200, headers=None):
        self._data = data
        self.status_code = status
        self.headers = headers or {}
        self.request = httpx.Request("POST", "https://fixed.example/v1/chat/completions")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("failed", request=self.request, response=self)

    def json(self):
        return self._data


class FakeClient:
    def __init__(self, response, calls, error=None):
        self.response = response
        self.calls = calls
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


def settings(**kwargs):
    values = {
        "gateway_api_key": "gateway-test",
        "answer_api_base_url": "https://fixed.example/v1",
        "answer_api_model": "fixed-model",
        "answer_api_id": "configured_api",
        "answer_api_key": "server-secret",
    }
    values.update(kwargs)
    return Settings(**values)


def test_answer_snapshot_uses_fixed_endpoint_and_server_key(monkeypatch):
    calls = []
    response = FakeResponse(
        {
            "model": "observed-model",
            "choices": [{"message": {"content": "Answer", "citations": ["https://example.com/?utm_source=x"]}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "ignored": "value"},
        }
    )
    monkeypatch.setattr(
        "app.services.answer_snapshot_service.build_client",
        lambda *_args, **_kwargs: FakeClient(response, calls),
    )
    service = AnswerSnapshotService(settings())

    result = asyncio.run(
        service.observe(AnswerSnapshotRequest(queries=["question"]), request_api_key="request-secret")
    )

    assert calls[0][0] == "https://fixed.example/v1/chat/completions"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer server-secret"
    assert result.success is True
    assert result.zero_persistence is True
    assert result.observations[0].answer == "Answer"
    assert result.observations[0].citations[0].url == "https://example.com/"
    assert result.usage.provider_usage == {"prompt_tokens": 10, "completion_tokens": 2}
    assert "request-secret" not in json.dumps(result.model_dump(mode="json"))


def test_answer_snapshot_uses_request_scoped_custom_endpoint_and_model(monkeypatch):
    calls = []
    response = FakeResponse({"choices": [{"message": {"content": "Custom answer"}}]})
    monkeypatch.setattr(
        "app.services.answer_snapshot_service.build_client",
        lambda *_args, **_kwargs: FakeClient(response, calls),
    )
    monkeypatch.setattr(
        "app.utils.url_normalization.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("8.8.8.8", 443))],
    )
    service = AnswerSnapshotService(settings())

    result = asyncio.run(
        service.observe(
            AnswerSnapshotRequest(
                queries=["question"],
                api_base_url="https://api.public-service.com/v1/chat/completions",
                api_model="custom-model",
            ),
            request_api_key="request-secret",
        )
    )

    assert calls[0][0] == "https://api.public-service.com/v1/chat/completions"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer request-secret"
    assert "server-secret" not in json.dumps(calls)
    assert result.api_id == "request_api"
    assert result.model == "custom-model"


def test_answer_snapshot_custom_config_requires_both_fields():
    with pytest.raises(ValidationError):
        AnswerSnapshotRequest(
            queries=["question"],
            api_base_url="https://api.public-service.com/v1",
        )

    with pytest.raises(ValidationError):
        AnswerSnapshotRequest(queries=["question"], api_model="custom-model")


def test_answer_snapshot_custom_config_requires_request_key():
    service = AnswerSnapshotService(settings())

    with pytest.raises(GatewayError) as exc_info:
        asyncio.run(
            service.observe(
                AnswerSnapshotRequest(
                    queries=["question"],
                    api_base_url="https://api.public-service.com/v1",
                    api_model="custom-model",
                )
            )
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "ANSWER_API_KEY_REQUIRED"


def test_answer_snapshot_rejects_unknown_endpoint_fields():
    with pytest.raises(ValidationError):
        AnswerSnapshotRequest(queries=["question"], base_url="https://attacker.example")


def test_answer_snapshot_requires_server_endpoint_and_model():
    service = AnswerSnapshotService(Settings(gateway_api_key="test"))

    with pytest.raises(GatewayError) as exc_info:
        asyncio.run(service.observe(AnswerSnapshotRequest(queries=["question"]), request_api_key="key"))

    assert exc_info.value.detail["code"] == "ANSWER_API_UNAVAILABLE"


def test_answer_snapshot_classifies_auth_failure_without_echoing_response(monkeypatch):
    calls = []
    response = FakeResponse({"error": "request-secret is invalid"}, status=401)
    monkeypatch.setattr(
        "app.services.answer_snapshot_service.build_client",
        lambda *_args, **_kwargs: FakeClient(response, calls),
    )
    service = AnswerSnapshotService(settings())

    result = asyncio.run(
        service.observe(AnswerSnapshotRequest(queries=["question"]), request_api_key="request-secret")
    )

    assert result.success is False
    assert result.errors[0].code == "ANSWER_API_AUTH_ERROR"
    assert result.errors[0].retryable is False
    assert "request-secret" not in json.dumps(result.model_dump(mode="json"))


def test_answer_snapshot_blocks_redirect_without_following(monkeypatch):
    calls = []
    client_options = {}
    response = FakeResponse({}, status=302, headers={"location": "https://private.example/v1"})

    def fake_build_client(*_args, **kwargs):
        client_options.update(kwargs)
        return FakeClient(response, calls)

    monkeypatch.setattr("app.services.answer_snapshot_service.build_client", fake_build_client)
    service = AnswerSnapshotService(settings())

    result = asyncio.run(service.observe(AnswerSnapshotRequest(queries=["question"])))

    assert client_options["follow_redirects"] is False
    assert result.errors[0].code == "ANSWER_API_REDIRECT_BLOCKED"
    assert "private.example" not in json.dumps(result.model_dump(mode="json"))


def test_answer_models_returns_only_clean_unique_bounded_ids(monkeypatch):
    calls = []
    model_items = [
        {"id": "model-a", "owned_by": "secret-owner", "metadata": {"endpoint": "hidden"}},
        {"id": "model-a"},
        {"id": "model-b"},
        {"id": "bad\nmodel"},
        {"id": "x" * 201},
    ] + [{"id": f"model-{index}"} for index in range(2, 150)]
    response = FakeResponse({"data": model_items})
    monkeypatch.setattr(
        "app.services.answer_snapshot_service.build_client",
        lambda *_args, **_kwargs: FakeClient(response, calls),
    )
    monkeypatch.setattr(
        "app.utils.url_normalization.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("8.8.8.8", 443))],
    )
    service = AnswerSnapshotService(settings())

    result = asyncio.run(
        service.list_models(
            AnswerModelsRequest(api_base_url="https://api.public-service.com/v1/chat/completions"),
            request_api_key="request-secret",
        )
    )

    assert calls[0][0] == "https://api.public-service.com/v1/models"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer request-secret"
    assert result.models[:2] == ["model-a", "model-b"]
    assert len(result.models) == 100
    serialized = json.dumps(result.model_dump(mode="json"))
    assert "owned_by" not in serialized
    assert "endpoint" not in serialized
    assert "request-secret" not in serialized
    assert "api.public-service.com" not in serialized


@pytest.mark.parametrize(
    ("response", "error", "expected_code", "expected_retryable"),
    [
        (FakeResponse({"error": "bad key"}, status=401), None, "ANSWER_API_AUTH_ERROR", False),
        (
            FakeResponse({"error": "quota"}, status=429, headers={"retry-after": "12"}),
            None,
            "ANSWER_API_RATE_LIMITED",
            True,
        ),
        (FakeResponse({"error": "down"}, status=503), None, "ANSWER_API_UPSTREAM_ERROR", True),
        (None, httpx.ReadTimeout("slow"), "ANSWER_API_TIMEOUT", True),
        (
            None,
            httpx.ConnectError("https://api.public-service.com failed"),
            "ANSWER_API_NETWORK_ERROR",
            True,
        ),
        (FakeResponse({}, status=302), None, "ANSWER_API_REDIRECT_BLOCKED", False),
        (FakeResponse({"error": "bad request"}, status=400), None, "ANSWER_API_INVALID_REQUEST", False),
        (FakeResponse({"unexpected": []}), None, "ANSWER_API_MALFORMED_RESPONSE", True),
    ],
)
def test_answer_models_sanitizes_failure_classes(
    monkeypatch,
    response,
    error,
    expected_code,
    expected_retryable,
):
    calls = []
    monkeypatch.setattr(
        "app.services.answer_snapshot_service.build_client",
        lambda *_args, **_kwargs: FakeClient(response, calls, error=error),
    )
    monkeypatch.setattr(
        "app.utils.url_normalization.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("8.8.8.8", 443))],
    )
    service = AnswerSnapshotService(settings())

    with pytest.raises(GatewayError) as exc_info:
        asyncio.run(
            service.list_models(
                AnswerModelsRequest(api_base_url="https://api.public-service.com/v1"),
                request_api_key="request-secret",
            )
        )

    assert exc_info.value.detail["code"] == expected_code
    assert exc_info.value.detail["retryable"] is expected_retryable
    serialized = json.dumps(exc_info.value.detail)
    assert "request-secret" not in serialized
    assert "api.public-service.com" not in serialized
