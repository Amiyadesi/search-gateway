import asyncio

import httpx

from app.config import Settings
from app.providers.tavily import TavilyProvider


def test_tavily_provider_configured_api_keys_dedupes_primary_and_extra():
    settings = Settings(
        gateway_api_key="test",
        tavily_api_key="one",
        tavily_api_keys="two, one, three",
    )

    assert TavilyProvider.configured_api_keys(settings) == ["one", "two", "three"]
    assert TavilyProvider.configured_upstream_count(settings) == 3


def test_tavily_provider_tries_next_key_after_failure(monkeypatch):
    provider = TavilyProvider(
        Settings(
            gateway_api_key="test",
            tavily_api_key="bad",
            tavily_api_keys="good",
        )
    )
    calls: list[str] = []

    class FakeResponse:
        def __init__(self, key: str):
            self.key = key
            self.request = httpx.Request("POST", "https://api.tavily.com/search")

        def raise_for_status(self) -> None:
            if self.key == "bad":
                raise httpx.HTTPStatusError(
                    "bad key",
                    request=self.request,
                    response=httpx.Response(401, request=self.request, text="bad"),
                )

        def json(self) -> dict:
            return {"results": [{"title": "Good", "url": "https://example.com", "content": "ok"}]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, _url, **kwargs):
            key = (kwargs.get("headers") or {})["Authorization"].removeprefix("Bearer ")
            calls.append(key)
            return FakeResponse(key)

    monkeypatch.setattr("app.providers.tavily.build_client", lambda *_args, **_kwargs: FakeClient())

    results = asyncio.run(provider.search("latest ai news", 1))

    assert calls == ["bad", "good"]
    assert results[0].title == "Good"
