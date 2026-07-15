import asyncio

from app.config import Settings
from app.providers.searxng import SearxngProvider


def test_searxng_provider_parses_json_results(monkeypatch):
    provider = SearxngProvider(Settings(gateway_api_key="test", searxng_enabled=True))
    calls = {"params": None, "headers": None}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "results": [
                    {"title": "One", "url": "https://example.com/1", "content": "first"},
                    {"title": "Two", "url": "https://example.com/2", "content": "second"},
                ]
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *_args, **kwargs):
            calls["params"] = kwargs.get("params")
            calls["headers"] = kwargs.get("headers")
            return FakeResponse()

    def fake_build_client(*_args, **_kwargs):
        return FakeClient()

    monkeypatch.setattr("app.providers.searxng.build_client", fake_build_client)
    results = asyncio.run(provider.search("undertale", 1))

    assert calls["params"] == {"q": "undertale", "format": "json"}
    assert calls["headers"] == {"Accept": "application/json"}
    assert len(results) == 1
    assert results[0].title == "One"
    assert results[0].url == "https://example.com/1"
    assert results[0].snippet == "first"
