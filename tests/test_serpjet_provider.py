import asyncio

import httpx
import pytest

from app.config import Settings
from app.providers.serpjet import SerpJetProvider
from app.utils.errors import GatewayError


def test_serpjet_provider_maps_google_organic_results(monkeypatch):
    provider = SerpJetProvider(
        Settings(
            gateway_api_key="test",
            serpjet_api_keys="server-key",
        )
    )
    observed: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "searchParameters": {"q": "evidence seo", "engine": "google"},
                "organic": [
                    {
                        "position": 1,
                        "title": "Evidence SEO",
                        "link": "https://example.com/report/?utm_source=test#result",
                        "snippet": "Observed result",
                    }
                ],
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, **kwargs):
            observed["url"] = url
            observed["headers"] = kwargs.get("headers")
            observed["params"] = kwargs.get("params")
            return FakeResponse()

    monkeypatch.setattr("app.providers.serpjet.build_client", lambda *_args, **_kwargs: FakeClient())

    results = asyncio.run(provider.search("evidence seo", 3))

    assert observed == {
        "url": "https://api.serpjet.io/v1/search",
        "headers": {"X-API-KEY": "server-key", "Accept": "application/json"},
        "params": {"q": "evidence seo", "engine": "google", "type": "search", "num": 3},
    }
    assert len(results) == 1
    assert str(results[0].url) == "https://example.com/report"
    assert results[0].title == "Evidence SEO"
    assert results[0].snippet == "Observed result"
    assert results[0].provider_metadata == {"engine": "google", "position": 1}


def test_serpjet_provider_tries_second_key_after_auth_failure(monkeypatch):
    provider = SerpJetProvider(
        Settings(
            gateway_api_key="test",
            serpjet_api_keys="expired-key,working-key",
        )
    )
    calls: list[str] = []

    class FakeResponse:
        def __init__(self, key: str):
            self.key = key
            self.request = httpx.Request("GET", "https://api.serpjet.io/v1/search")

        def raise_for_status(self) -> None:
            if self.key == "expired-key":
                raise httpx.HTTPStatusError(
                    "unauthorized",
                    request=self.request,
                    response=httpx.Response(401, request=self.request, text="credential rejected"),
                )

        def json(self) -> dict:
            return {
                "organic": [
                    {
                        "position": 1,
                        "title": "Fallback worked",
                        "link": "https://example.com/fallback",
                        "snippet": "ok",
                    }
                ]
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, _url, **kwargs):
            key = (kwargs.get("headers") or {})["X-API-KEY"]
            calls.append(key)
            return FakeResponse(key)

    monkeypatch.setattr("app.providers.serpjet.build_client", lambda *_args, **_kwargs: FakeClient())

    results = asyncio.run(provider.search("fallback query", 1))

    assert calls == ["expired-key", "working-key"]
    assert results[0].title == "Fallback worked"


@pytest.mark.parametrize("status", [402, 403, 429, 500, 503])
def test_serpjet_provider_tries_second_key_for_retryable_upstream_status(monkeypatch, status):
    provider = SerpJetProvider(
        Settings(gateway_api_key="test", serpjet_api_keys="first-key,second-key")
    )
    calls: list[str] = []

    class FakeResponse:
        def __init__(self, key: str):
            self.key = key
            self.request = httpx.Request("GET", "https://api.serpjet.io/v1/search")

        def raise_for_status(self) -> None:
            if self.key == "first-key":
                raise httpx.HTTPStatusError(
                    "retryable",
                    request=self.request,
                    response=httpx.Response(status, request=self.request),
                )

        def json(self) -> dict:
            return {"organic": []}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, _url, **kwargs):
            key = (kwargs.get("headers") or {})["X-API-KEY"]
            calls.append(key)
            return FakeResponse(key)

    monkeypatch.setattr("app.providers.serpjet.build_client", lambda *_args, **_kwargs: FakeClient())

    assert asyncio.run(provider.search("query", 1)) == []
    assert calls == ["first-key", "second-key"]


def test_serpjet_provider_tries_second_key_after_timeout(monkeypatch):
    provider = SerpJetProvider(
        Settings(gateway_api_key="test", serpjet_api_keys="first-key,second-key")
    )
    calls: list[str] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"organic": []}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, _url, **kwargs):
            key = (kwargs.get("headers") or {})["X-API-KEY"]
            calls.append(key)
            if key == "first-key":
                raise httpx.ReadTimeout("timeout")
            return FakeResponse()

    monkeypatch.setattr("app.providers.serpjet.build_client", lambda *_args, **_kwargs: FakeClient())

    assert asyncio.run(provider.search("query", 1)) == []
    assert calls == ["first-key", "second-key"]


def test_serpjet_provider_tries_second_key_after_network_failure(monkeypatch):
    provider = SerpJetProvider(
        Settings(gateway_api_key="test", serpjet_api_keys="first-key,second-key")
    )
    calls: list[str] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"organic": []}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, _url, **kwargs):
            key = (kwargs.get("headers") or {})["X-API-KEY"]
            calls.append(key)
            if key == "first-key":
                raise httpx.ConnectError("connection failed")
            return FakeResponse()

    monkeypatch.setattr("app.providers.serpjet.build_client", lambda *_args, **_kwargs: FakeClient())

    assert asyncio.run(provider.search("query", 1)) == []
    assert calls == ["first-key", "second-key"]


def test_serpjet_provider_does_not_retry_invalid_request(monkeypatch):
    provider = SerpJetProvider(
        Settings(gateway_api_key="test", serpjet_api_keys="first-key,second-key")
    )
    calls: list[str] = []

    class FakeResponse:
        def __init__(self):
            self.request = httpx.Request("GET", "https://api.serpjet.io/v1/search")

        def raise_for_status(self) -> None:
            raise httpx.HTTPStatusError(
                "bad request",
                request=self.request,
                response=httpx.Response(400, request=self.request, text="secret upstream body"),
            )

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, _url, **kwargs):
            calls.append((kwargs.get("headers") or {})["X-API-KEY"])
            return FakeResponse()

    monkeypatch.setattr("app.providers.serpjet.build_client", lambda *_args, **_kwargs: FakeClient())

    with pytest.raises(GatewayError) as caught:
        asyncio.run(provider.search("query", 1))

    assert calls == ["first-key"]
    assert caught.value.detail == {"status": 400}
    assert "first-key" not in str(caught.value)
    assert "secret upstream body" not in str(caught.value)


def test_serpjet_provider_dedupes_and_bounds_configured_keys():
    settings = Settings(
        gateway_api_key="test",
        serpjet_api_keys="one, two, one, three",
    )

    assert SerpJetProvider.configured_api_keys(settings) == ["one", "two"]
    assert SerpJetProvider.configured_upstream_count(settings) == 2
