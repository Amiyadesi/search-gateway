from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app
from app.routes import mcp


class FakeSearchAdapter:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    def handle(self, message: dict) -> dict | None:
        self.messages.append(message)
        if "id" not in message:
            return None
        if message.get("method") == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {"tools": [{"name": "ai_search"}]},
            }
        return {
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": {"content": [{"type": "text", "text": "ok"}]},
        }


def _client(monkeypatch, *, fns_token: str = "fns-secret"):
    adapter = FakeSearchAdapter()
    monkeypatch.setattr(mcp, "_SEARCH_ADAPTER", adapter)
    mcp.MCP_RATE_LIMITER._requests.clear()
    app.dependency_overrides[get_settings] = lambda: Settings(
        gateway_api_key="gateway-secret",
        mcp_access_token="mcp-secret",
        fns_mcp_token=fns_token,
        mcp_rate_limit_per_minute=20,
    )
    return TestClient(app), adapter


def test_search_mcp_requires_separate_bearer_token(monkeypatch):
    client, _ = _client(monkeypatch)
    try:
        denied = client.post("/mcp/search", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        allowed = client.post(
            "/mcp/search",
            headers={"Authorization": "Bearer mcp-secret"},
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        )
    finally:
        app.dependency_overrides.clear()

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["result"]


def test_search_mcp_dispatches_json_rpc_and_sse(monkeypatch):
    client, adapter = _client(monkeypatch)
    headers = {"Authorization": "Bearer mcp-secret"}
    try:
        initialized = client.post(
            "/mcp/search",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        listed = client.post(
            "/mcp/search",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
        sse = client.post(
            "/mcp/search",
            headers={**headers, "Accept": "text/event-stream"},
            json={"jsonrpc": "2.0", "id": 3, "method": "ping"},
        )
        notification = client.post(
            "/mcp/search",
            headers=headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
    finally:
        app.dependency_overrides.clear()

    assert initialized.status_code == 200
    assert listed.json()["result"]["tools"][0]["name"] == "ai_search"
    assert sse.status_code == 200
    assert "data:" in sse.text
    assert notification.status_code == 202
    assert [item.get("id") for item in adapter.messages] == [1, 2, 3, None]


def test_unknown_mcp_route_is_not_a_server_alias(monkeypatch):
    client, _ = _client(monkeypatch)
    try:
        response = client.post(
            "/mcp/unknown",
            headers={"Authorization": "Bearer mcp-secret"},
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 404


class FakeUpstreamResponse:
    status_code = 200
    headers = {
        "content-type": "text/event-stream",
        "cache-control": "no-cache",
        "mcp-session-id": "session-1",
    }

    async def aiter_raw(self):
        yield b"data: upstream\n\n"

    async def aclose(self):
        return None


class FakeHttpClient:
    requests: list[httpx.Request] = []

    def __init__(self, *args, **kwargs):
        del args, kwargs

    def build_request(self, method, url, **kwargs):
        request = httpx.Request(method, url, **kwargs)
        self.requests.append(request)
        return request

    async def send(self, request, stream=True):
        assert stream is True
        return FakeUpstreamResponse()

    async def aclose(self):
        return None


def test_fns_destructive_call_requires_one_shot_confirmation(monkeypatch):
    client, _ = _client(monkeypatch)
    FakeHttpClient.requests = []
    monkeypatch.setattr(mcp.httpx, "AsyncClient", FakeHttpClient)
    headers = {"Authorization": "Bearer mcp-secret", "Content-Type": "application/json"}
    request = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": "file_delete", "arguments": {"path": "/vault/a.md"}},
    }
    try:
        first = client.post("/mcp/fns", headers=headers, json=request)
        confirmation = json.loads(first.json()["result"]["content"][0]["text"])
        request["params"]["arguments"]["__gateway_confirmation_id"] = confirmation[
            "confirmation_id"
        ]
        second = client.post("/mcp/fns", headers=headers, json=request)
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 200
    assert confirmation["code"] == "CONFIRMATION_REQUIRED"
    assert second.status_code == 200
    assert FakeHttpClient.requests
    forwarded = json.loads(FakeHttpClient.requests[-1].content)
    assert "__gateway_confirmation_id" not in forwarded["params"]["arguments"]
    assert forwarded["params"]["arguments"] == {"path": "/vault/a.md"}
    assert FakeHttpClient.requests[-1].headers["authorization"] == "Bearer fns-secret"
    assert FakeHttpClient.requests[-1].headers["x-client"] == "AstrBot"
    assert "mcp-secret" not in str(FakeHttpClient.requests[-1].headers)
