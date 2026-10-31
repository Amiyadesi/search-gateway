import asyncio

import pytest

from app.config import Settings
from app.providers.context_dev import ContextDevProvider
from app.utils.errors import GatewayError


def test_context_dev_provider_requests_minimum_result_count_and_maps_results(monkeypatch):
    provider = ContextDevProvider(Settings(gateway_api_key="test", context_dev_api_key="ctx"))
    calls = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "results": [
                    {"title": "A", "url": "https://example.com/a", "description": "alpha"},
                    {"title": "B", "url": "https://example.com/b", "description": "beta"},
                ]
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

    monkeypatch.setattr("app.providers.context_dev.build_client", lambda *_args, **_kwargs: FakeClient())

    results = asyncio.run(provider.search("test", 1))

    assert calls == [
        (
            "https://api.context.dev/v1/web/search",
            {
                "headers": {"Authorization": "Bearer ctx", "Accept": "application/json"},
                "json": {"query": "test", "numResults": 10},
            },
        )
    ]
    assert len(results) == 1
    assert results[0].title == "A"
    assert results[0].url == "https://example.com/a"
    assert results[0].snippet == "alpha"


def test_context_dev_provider_requires_an_api_key():
    provider = ContextDevProvider(Settings(gateway_api_key="test"))

    with pytest.raises(GatewayError, match="Context.dev API Key 未配置"):
        asyncio.run(provider.search("test", 1))
