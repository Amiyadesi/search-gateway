import asyncio

import httpx

from app.config import Settings
from app.schemas.common import SearchResult
from app.providers.grok import GrokUpstream
from app.providers.grok import GrokProvider


def test_grok_provider_prefers_structured_search_sources(monkeypatch):
    provider = GrokProvider(
        Settings(gateway_api_key="test", grok_search_enabled=True, grok_api_key="gk", grok_base_url="http://grok/v1")
    )
    calls = {"url": "", "headers": {}, "json": {}}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "search_sources": [
                    {"title": "One", "url": "https://example.com/1", "snippet": "first"},
                    {"title": "Two", "url": "https://example.com/2", "description": "second"},
                ],
                "choices": [{"message": {"content": "ignored"}}],
            }

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

    monkeypatch.setattr("app.providers.grok.build_client", fake_build_client)
    results = asyncio.run(provider.search("latest ai news", 1))

    assert calls["url"] == "http://grok/v1/chat/completions"
    assert calls["headers"]["Authorization"] == "Bearer gk"
    assert calls["json"]["stream"] is False
    assert calls["json"]["max_tokens"] == 1200
    assert calls["json"]["messages"][1]["content"].startswith("Search the web for: latest ai news")
    assert len(results) == 1
    assert results[0].title == "One"
    assert results[0].url == "https://example.com/1"
    assert results[0].snippet == "first"


def test_grok_provider_normalizes_json_upstreams():
    settings = Settings(
        gateway_api_key="test",
        grok_upstreams=(
            '[{"name":"one","base_url":"https://one.example","api_key":"k1"},'
            '{"base_url":"https://two.example/v1","api_key":"k2"}]'
        ),
    )

    upstreams = GrokProvider.configured_upstreams(settings)

    assert upstreams == [
        GrokUpstream(name="one", base_url="https://one.example/v1", api_key="k1"),
        GrokUpstream(name="upstream-2", base_url="https://two.example/v1", api_key="k2"),
    ]


def test_grok_provider_supports_per_upstream_model(monkeypatch):
    provider = GrokProvider(
        Settings(
            gateway_api_key="test",
            grok_search_enabled=True,
            grok_upstreams='[{"name":"one","base_url":"https://one.example","api_key":"k1","model":"grok-custom"}]',
            grok_search_model="grok-default",
        )
    )
    calls = {"json": {}}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"search_sources": [{"title": "One", "url": "https://example.com/1", "snippet": "ok"}]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, _url, **kwargs):
            calls["json"] = kwargs.get("json") or {}
            return FakeResponse()

    monkeypatch.setattr("app.providers.grok.build_client", lambda *_args, **_kwargs: FakeClient())

    results = asyncio.run(provider.search("latest ai news", 1))

    assert calls["json"]["model"] == "grok-custom"
    assert results[0].title == "One"


def test_grok_provider_falls_back_to_legacy_single_upstream():
    settings = Settings(
        gateway_api_key="test",
        grok_base_url="https://legacy.example",
        grok_api_key="legacy-key",
    )

    upstreams = GrokProvider.configured_upstreams(settings)

    assert upstreams == [
        GrokUpstream(name="legacy.example", base_url="https://legacy.example/v1", api_key="legacy-key")
    ]


def test_grok_provider_tries_next_upstream_after_failure(monkeypatch):
    provider = GrokProvider(
        Settings(
            gateway_api_key="test",
            grok_search_enabled=True,
            grok_upstreams=(
                '[{"name":"bad","base_url":"https://bad.example","api_key":"bad-key"},'
                '{"name":"good","base_url":"https://good.example","api_key":"good-key"}]'
            ),
        )
    )
    calls = []

    class FakeResponse:
        def __init__(self, url):
            self.url = url
            self.status_code = 200
            self.text = "{}"
            self.request = httpx.Request("POST", url)

        def raise_for_status(self) -> None:
            if "bad.example" in self.url:
                raise httpx.HTTPStatusError(
                    "bad upstream",
                    request=self.request,
                    response=httpx.Response(502, request=self.request, text="bad"),
                )

        def json(self) -> dict:
            return {
                "search_sources": [
                    {"title": "Good", "url": "https://example.com/good", "snippet": "ok"},
                ]
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            calls.append({"url": url, "headers": kwargs.get("headers") or {}})
            return FakeResponse(url)

    monkeypatch.setattr("app.providers.grok.build_client", lambda *_args, **_kwargs: FakeClient())

    results = asyncio.run(provider.search("latest ai news", 1))

    assert [call["url"] for call in calls] == [
        "https://bad.example/v1/chat/completions",
        "https://good.example/v1/chat/completions",
    ]
    assert calls[0]["headers"]["Authorization"] == "Bearer bad-key"
    assert calls[1]["headers"]["Authorization"] == "Bearer good-key"
    assert results[0].title == "Good"


def test_grok_provider_uses_groksearch_bridge(monkeypatch):
    provider = GrokProvider(
        Settings(
            gateway_api_key="test",
            grok_search_enabled=True,
            grok_backend="groksearch",
            groksearch_bridge_url="http://bridge:8010/",
            groksearch_extra_sources=4,
        )
    )
    calls = {"url": "", "json": {}}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "success": True,
                "results": [
                    {"title": "Bridge", "url": "https://example.com/bridge", "snippet": "from bridge"},
                ],
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            calls["url"] = url
            calls["json"] = kwargs.get("json") or {}
            return FakeResponse()

    monkeypatch.setattr("app.providers.grok.build_client", lambda *_args, **_kwargs: FakeClient())

    results = asyncio.run(provider.search("latest ai news", 2))

    assert calls["url"] == "http://bridge:8010/search"
    assert calls["json"] == {
        "query": "latest ai news",
        "max_results": 2,
        "model": "grok-4.20-0309-non-reasoning-console",
        "extra_sources": 4,
    }
    assert results[0].title == "Bridge"


def test_grok_provider_hybrid_falls_back_to_openai_upstream(monkeypatch):
    provider = GrokProvider(
        Settings(
            gateway_api_key="test",
            grok_search_enabled=True,
            grok_backend="hybrid",
            grok_api_key="gk",
            grok_base_url="http://grok/v1",
        )
    )
    calls: list[str] = []

    async def fake_bridge(*_args, **_kwargs):
        calls.append("bridge")
        return []

    async def fake_upstreams(*_args, **_kwargs):
        calls.append("openai")
        return [SearchResult(title="Legacy", url="https://example.com/legacy", snippet="ok")]

    monkeypatch.setattr(provider, "_request_groksearch_bridge", fake_bridge)
    monkeypatch.setattr(provider, "_search_openai_upstreams", fake_upstreams)

    results = asyncio.run(provider.search("latest ai news", 1))

    assert calls == ["bridge", "openai"]
    assert results[0].title == "Legacy"


def test_grok_provider_backend_readiness_counts_bridge_and_upstreams():
    disabled = Settings(gateway_api_key="test", grok_backend="hybrid", grok_api_key="gk")
    assert GrokProvider.configured_backend_ready(disabled) is False

    bridge_only = Settings(
        gateway_api_key="test",
        grok_search_enabled=True,
        grok_backend="groksearch",
        groksearch_bridge_url="http://bridge:8010",
    )
    assert GrokProvider.configured_backend_ready(bridge_only) is True
    assert GrokProvider.configured_backend_count(bridge_only) == 1

    hybrid = Settings(
        gateway_api_key="test",
        grok_search_enabled=True,
        grok_backend="hybrid",
        grok_api_key="gk",
        grok_base_url="https://grok.example/v1",
        groksearch_bridge_url="http://bridge:8010",
    )
    assert GrokProvider.configured_backend_ready(hybrid) is True
    assert GrokProvider.configured_backend_count(hybrid) == 2


def test_grok_provider_parses_message_json_fallback():
    parsed = GrokProvider._results_from_message(
        {
            "choices": [
                {
                    "message": {
                        "content": (
                            '```json\n'
                            '[{"title":"One","url":"https://example.com/1","snippet":"first"}]\n'
                            '```'
                        )
                    }
                }
            ]
        },
        max_results=5,
    )

    assert len(parsed) == 1
    assert parsed[0].title == "One"


def test_grok_provider_parses_markdown_links_when_message_is_not_json():
    parsed = GrokProvider._results_from_message(
        {
            "choices": [
                {
                    "message": {
                        "content": (
                            "Latest from OpenAI: see [OpenAI news](https://openai.com/news/) "
                            "and https://example.com/report."
                        )
                    }
                }
            ]
        },
        max_results=2,
    )

    assert len(parsed) == 2
    assert parsed[0].title == "OpenAI news"
    assert parsed[0].url == "https://openai.com/news/"
    assert parsed[1].url == "https://example.com/report"


def test_grok_provider_parses_citation_links_that_look_like_json_arrays():
    parsed = GrokProvider._results_from_message(
        {
            "choices": [
                {
                    "message": {
                        "content": (
                            "OpenAI has a recent update.[[1]]"
                            "(https://www.youtube.com/watch?v=IB8W948Usig)"
                        )
                    }
                }
            ]
        },
        max_results=1,
    )

    assert len(parsed) == 1
    assert parsed[0].url == "https://www.youtube.com/watch?v=IB8W948Usig"
