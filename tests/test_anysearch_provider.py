import asyncio

import httpx

from app.config import Settings
from app.providers.anysearch import AnySearchProvider


def test_anysearch_keys_are_deduped_and_bounded():
    settings = Settings(
        gateway_api_key="test",
        anysearch_enabled=True,
        anysearch_api_key="one",
        anysearch_api_keys="two,one,three",
    )

    assert AnySearchProvider.configured_api_keys(settings) == ["one", "two"]
    assert AnySearchProvider.configured_upstream_count(settings) == 2


def test_anysearch_search_tries_second_key_and_preserves_vertical_payload(monkeypatch):
    provider = AnySearchProvider(
        Settings(
            gateway_api_key="test",
            anysearch_enabled=True,
            anysearch_api_key="bad",
            anysearch_api_keys="good",
            anysearch_api_url="https://search.example",
        )
    )
    calls: list[tuple[str, dict]] = []

    class FakeResponse:
        def __init__(self, key: str):
            self.key = key
            self.request = httpx.Request("POST", "https://search.example/v1/search")

        def raise_for_status(self) -> None:
            if self.key == "bad":
                raise httpx.HTTPStatusError(
                    "unauthorized",
                    request=self.request,
                    response=httpx.Response(401, request=self.request),
                )

        def json(self) -> dict:
            return {
                "code": 0,
                "data": {
                    "results": [
                        {"title": "AAPL", "url": "https://example.com/aapl", "content": "quote"}
                    ]
                },
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, *, headers, **kwargs):
            calls.append((headers["Authorization"], kwargs["json"]))
            return FakeResponse(headers["Authorization"].removeprefix("Bearer "))

    monkeypatch.setattr("app.providers.anysearch.build_client", lambda *_args, **_kwargs: FakeClient())

    results = asyncio.run(
        provider.search(
            "AAPL",
            3,
            tag="finance.quote",
            params={"type": "stock", "symbol": "AAPL", "cn_code": ""},
        )
    )

    assert [item[0] for item in calls] == ["Bearer bad", "Bearer good"]
    assert calls[-1][1] == {
        "query": "AAPL",
        "max_results": 3,
        "tag": "finance.quote",
        "params": {"type": "stock", "symbol": "AAPL", "cn_code": ""},
    }
    assert results[0].title == "AAPL"


def test_anysearch_sub_domains_maps_domain_directory(monkeypatch):
    provider = AnySearchProvider(
        Settings(
            gateway_api_key="test",
            anysearch_enabled=True,
            anysearch_api_key="key",
            anysearch_api_url="https://search.example",
        )
    )

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 0, "data": {"domains": [{"domain": "finance"}]}}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, **kwargs):
            assert method == "GET"
            assert kwargs["params"] == [("domain", "finance"), ("domain", "security")]
            return FakeResponse()

    monkeypatch.setattr("app.providers.anysearch.build_client", lambda *_args, **_kwargs: FakeClient())

    assert asyncio.run(provider.sub_domains(["finance", "security"])) == [{"domain": "finance"}]
