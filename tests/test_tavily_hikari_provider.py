import asyncio

from app.config import Settings
from app.providers.tavily_hikari import TavilyHikariProvider


def test_tavily_hikari_provider_parses_sse_structured_results(monkeypatch):
    provider = TavilyHikariProvider(
        Settings(gateway_api_key="test", tavily_hikari_token="token", tavily_hikari_url="https://example.com/mcp")
    )
    calls = {"url": "", "headers": {}, "json": {}}

    class FakeResponse:
        text = (
            'event: message\n'
            'data: {"jsonrpc":"2.0","id":1,"result":{"structuredContent":{"results":['
            '{"title":"One","url":"https://example.com/1","content":"first"},'
            '{"title":"Two","url":"https://example.com/2","content":"second"}'
            "]}}}\n"
        )

        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            calls["url"] = url
            calls["headers"] = kwargs.get("headers") or {}
            calls["json"] = kwargs.get("json") or {}
            return FakeResponse()

    def fake_build_client(*_args, **_kwargs):
        return FakeClient()

    monkeypatch.setattr("app.providers.tavily_hikari.build_client", fake_build_client)
    results = asyncio.run(provider.search("vaultwarden websocket", 1))

    assert calls["url"] == "https://example.com/mcp"
    assert calls["headers"]["Authorization"] == "Bearer token"
    assert calls["headers"]["Accept"] == "application/json, text/event-stream"
    assert calls["json"]["method"] == "tools/call"
    assert calls["json"]["params"]["name"] == "tavily_search"
    assert calls["json"]["params"]["arguments"]["search_depth"] == "fast"
    assert "include_answer" not in calls["json"]["params"]["arguments"]
    assert len(results) == 1
    assert results[0].title == "One"
    assert results[0].url == "https://example.com/1"
    assert results[0].snippet == "first"


def test_tavily_hikari_provider_parses_content_text_fallback():
    parsed = TavilyHikariProvider._results_from_mcp_result(
        {
            "content": [
                {
                    "type": "text",
                    "text": '{"results":[{"title":"One","url":"https://example.com/1","content":"first"}]}',
                }
            ]
        },
        max_results=5,
    )

    assert len(parsed) == 1
    assert parsed[0].title == "One"
