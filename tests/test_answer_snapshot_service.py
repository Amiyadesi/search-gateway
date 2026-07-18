import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.schemas.evidence import AnswerModelsRequest, AnswerSnapshotRequest
from app.services.answer_snapshot_service import AnswerSnapshotService, _AnswerTimingTrace
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


class TraceFailingClient(FakeClient):
    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        trace = kwargs["extensions"]["trace"]
        await trace("http11.receive_response_headers.started", {"request": "request-secret"})
        await trace("http11.receive_response_headers.failed", {"exception": "request-secret"})
        raise httpx.ReadTimeout("slow upstream")


class SlowAnswerHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("content-length", "0"))
        self.rfile.read(content_length)
        time.sleep(0.05)
        body = json.dumps({"choices": [{"message": {"content": "slow answer"}}]}).encode()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            time.sleep(0.05)
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_args):
        pass


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


def test_answer_snapshot_requests_a_concise_final_answer_without_reasoning(monkeypatch):
    calls = []
    response = FakeResponse({"choices": [{"message": {"content": "Final answer"}}]})
    monkeypatch.setattr(
        "app.services.answer_snapshot_service.build_client",
        lambda *_args, **_kwargs: FakeClient(response, calls),
    )
    service = AnswerSnapshotService(settings())

    result = asyncio.run(service.observe(AnswerSnapshotRequest(queries=["question"])))

    assert result.success is True
    system_prompt = calls[0][1]["json"]["messages"][0]["content"]
    assert "concise final answer" in system_prompt
    assert "Do not include chain-of-thought" in system_prompt
    assert "No verifiable answer available." in system_prompt


def test_answer_snapshot_distinguishes_exhausted_reasoning_without_final_content(monkeypatch):
    calls = []
    response = FakeResponse(
        {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {
                        "content": None,
                        "reasoning_content": "private reasoning that must not be returned",
                    },
                }
            ]
        }
    )
    monkeypatch.setattr(
        "app.services.answer_snapshot_service.build_client",
        lambda *_args, **_kwargs: FakeClient(response, calls),
    )
    service = AnswerSnapshotService(settings())

    result = asyncio.run(service.observe(AnswerSnapshotRequest(queries=["question"])))
    serialized = json.dumps(result.model_dump(mode="json"))

    assert result.success is False
    assert result.errors[0].code == "ANSWER_API_NO_FINAL_CONTENT"
    assert result.errors[0].retryable is False
    assert "reasoning_content" not in serialized
    assert "private reasoning" not in serialized


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
    request_data = {key: value for key, value in calls[0][1].items() if key != "extensions"}
    assert "server-secret" not in json.dumps(request_data)
    assert result.api_id == "request_api"
    assert result.model == "custom-model"


def test_answer_snapshot_adds_v1_for_request_scoped_origin(monkeypatch):
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
                api_base_url="https://api.public-service.com",
                api_model="custom-model",
            ),
            request_api_key="request-secret",
        )
    )

    assert result.success is True
    assert calls[0][0] == "https://api.public-service.com/v1/chat/completions"


def test_answer_snapshot_adds_v1_for_configured_origin(monkeypatch):
    calls = []
    response = FakeResponse({"choices": [{"message": {"content": "Configured answer"}}]})
    monkeypatch.setattr(
        "app.services.answer_snapshot_service.build_client",
        lambda *_args, **_kwargs: FakeClient(response, calls),
    )
    service = AnswerSnapshotService(settings(answer_api_base_url="https://fixed.example"))

    result = asyncio.run(service.observe(AnswerSnapshotRequest(queries=["question"])))

    assert result.success is True
    assert calls[0][0] == "https://fixed.example/v1/chat/completions"


def test_answer_timing_trace_aggregates_supported_phases_without_metadata():
    trace = _AnswerTimingTrace()
    events = [
        ("connection.connect_tcp.started", 1.000),
        ("connection.connect_tcp.complete", 1.012),
        ("connection.start_tls.started", 1.012),
        ("connection.start_tls.complete", 1.020),
        ("http11.send_request_headers.started", 1.020),
        ("http11.send_request_headers.complete", 1.022),
        ("http11.send_request_body.started", 1.022),
        ("http11.send_request_body.complete", 1.025),
        ("http11.receive_response_headers.started", 1.025),
        ("http11.receive_response_headers.complete", 1.095),
        ("http11.receive_response_body.started", 1.095),
        ("http11.receive_response_body.complete", 1.135),
    ]
    for event, observed_at in events:
        trace.record(event, at=observed_at)

    timing = trace.snapshot(total_ms=140)

    assert timing.model_dump() == {
        "connect_ms": 20,
        "request_write_ms": 5,
        "upstream_wait_ms": 70,
        "response_read_ms": 40,
        "total_ms": 140,
        "upstream_wait_is_approximation": True,
    }
    assert "request-secret" not in repr(trace.__dict__)


def test_answer_timing_trace_allows_reused_connection_without_connect_events():
    trace = _AnswerTimingTrace()
    trace.record("http2.send_request_headers.started", at=2.000)
    trace.record("http2.send_request_headers.complete", at=2.004)
    trace.record("http2.receive_response_headers.started", at=2.004)
    trace.record("http2.receive_response_headers.complete", at=2.014)

    timing = trace.snapshot(total_ms=20)

    assert timing.connect_ms is None
    assert timing.request_write_ms == 4
    assert timing.upstream_wait_ms == 10


def test_answer_timing_trace_retains_failed_observed_phase():
    trace = _AnswerTimingTrace()
    trace.record("http11.receive_response_headers.started", at=3.000)
    trace.record("http11.receive_response_headers.failed", at=3.030)

    timing = trace.snapshot(total_ms=30)

    assert trace.timeout_phase() == "upstream"
    assert timing.upstream_wait_ms == 30


def test_answer_snapshot_uses_observed_timeout_phase(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.services.answer_snapshot_service.build_client",
        lambda *_args, **_kwargs: TraceFailingClient(None, calls),
    )
    service = AnswerSnapshotService(settings())

    result = asyncio.run(service.observe(AnswerSnapshotRequest(queries=["question"])))

    assert result.success is False
    assert result.errors[0].timeout_phase == "upstream"
    assert result.observations[0].timing.upstream_wait_ms is not None
    assert "request-secret" not in json.dumps(result.model_dump(mode="json"))


def test_answer_snapshot_reports_real_local_phase_timings():
    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowAnswerHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        service = AnswerSnapshotService(
            settings(
                answer_api_base_url=f"http://127.0.0.1:{server.server_port}/v1",
                answer_api_timeout_seconds=1,
            )
        )
        result = asyncio.run(service.observe(AnswerSnapshotRequest(queries=["question"])))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

    assert result.success is True
    timing = result.observations[0].timing
    assert timing is not None
    assert timing.connect_ms is not None
    assert timing.request_write_ms is not None
    assert timing.upstream_wait_ms is not None and timing.upstream_wait_ms >= 30
    assert timing.response_read_ms is not None and timing.response_read_ms >= 30
    assert timing.total_ms == result.observations[0].latency_ms


def test_answer_snapshot_reports_real_local_upstream_timeout_timing():
    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowAnswerHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        service = AnswerSnapshotService(
            settings(
                answer_api_base_url=f"http://127.0.0.1:{server.server_port}/v1",
                answer_api_timeout_seconds=0.02,
            )
        )
        result = asyncio.run(service.observe(AnswerSnapshotRequest(queries=["question"])))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

    assert result.success is False
    assert result.errors[0].timeout_phase == "upstream"
    timing = result.observations[0].timing
    assert timing is not None
    assert timing.upstream_wait_ms is not None and timing.upstream_wait_ms >= 10
    assert timing.response_read_ms is None


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


@pytest.mark.parametrize(
    ("error", "expected_phase"),
    [
        (httpx.ConnectTimeout("slow connect"), "connect"),
        (httpx.WriteTimeout("slow write"), "write"),
        (httpx.ReadTimeout("slow read"), "read"),
        (httpx.PoolTimeout("slow pool"), "pool"),
    ],
)
def test_answer_snapshot_reports_transport_timeout_phase(monkeypatch, error, expected_phase):
    calls = []
    monkeypatch.setattr(
        "app.services.answer_snapshot_service.build_client",
        lambda *_args, **_kwargs: FakeClient(None, calls, error=error),
    )
    service = AnswerSnapshotService(settings())

    result = asyncio.run(service.observe(AnswerSnapshotRequest(queries=["question"])))

    assert result.success is False
    assert result.errors[0].code == "ANSWER_API_TIMEOUT"
    assert result.errors[0].timeout_phase == expected_phase


@pytest.mark.parametrize(
    ("status", "expected_phase"),
    [(408, "upstream"), (504, "gateway")],
)
def test_answer_snapshot_reports_upstream_timeout_phase(monkeypatch, status, expected_phase):
    calls = []
    monkeypatch.setattr(
        "app.services.answer_snapshot_service.build_client",
        lambda *_args, **_kwargs: FakeClient(FakeResponse({}, status=status), calls),
    )
    service = AnswerSnapshotService(settings())

    result = asyncio.run(service.observe(AnswerSnapshotRequest(queries=["question"])))

    assert result.success is False
    assert result.errors[0].code == "ANSWER_API_TIMEOUT"
    assert result.errors[0].timeout_phase == expected_phase


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
    ("response", "error", "expected_code", "expected_retryable", "expected_timeout_phase"),
    [
        (FakeResponse({"error": "bad key"}, status=401), None, "ANSWER_API_AUTH_ERROR", False, None),
        (
            FakeResponse({"error": "quota"}, status=429, headers={"retry-after": "12"}),
            None,
            "ANSWER_API_RATE_LIMITED",
            True,
            None,
        ),
        (FakeResponse({"error": "down"}, status=503), None, "ANSWER_API_UPSTREAM_ERROR", True, None),
        (None, httpx.ReadTimeout("slow"), "ANSWER_API_TIMEOUT", True, "read"),
        (
            FakeResponse({"error": "upstream timeout"}, status=408),
            None,
            "ANSWER_API_TIMEOUT",
            True,
            "upstream",
        ),
        (
            FakeResponse({"error": "gateway timeout"}, status=504),
            None,
            "ANSWER_API_TIMEOUT",
            True,
            "gateway",
        ),
        (
            None,
            httpx.ConnectError("https://api.public-service.com failed"),
            "ANSWER_API_NETWORK_ERROR",
            True,
            None,
        ),
        (FakeResponse({}, status=302), None, "ANSWER_API_REDIRECT_BLOCKED", False, None),
        (FakeResponse({"error": "bad request"}, status=400), None, "ANSWER_API_INVALID_REQUEST", False, None),
        (FakeResponse({"unexpected": []}), None, "ANSWER_API_MALFORMED_RESPONSE", True, None),
    ],
)
def test_answer_models_sanitizes_failure_classes(
    monkeypatch,
    response,
    error,
    expected_code,
    expected_retryable,
    expected_timeout_phase,
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
    assert exc_info.value.detail.get("timeout_phase") == expected_timeout_phase
    serialized = json.dumps(exc_info.value.detail)
    assert "request-secret" not in serialized
    assert "api.public-service.com" not in serialized
