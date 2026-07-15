import asyncio

from app.config import Settings
from app.providers.stackexchange import StackExchangeProvider


def test_stackexchange_provider_parses_advanced_search_results(monkeypatch):
    provider = StackExchangeProvider(Settings(gateway_api_key="test", stackexchange_key="se-key"))
    calls = {"url": "", "headers": {}, "params": {}}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "items": [
                    {
                        "title": "How to use FastAPI dependency injection?",
                        "link": "https://stackoverflow.com/questions/1/how-to-use-fastapi",
                        "score": 12,
                        "answer_count": 3,
                        "tags": ["python", "fastapi"],
                    }
                ]
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, **kwargs):
            calls["url"] = url
            calls["headers"] = kwargs.get("headers") or {}
            calls["params"] = kwargs.get("params") or {}
            return FakeResponse()

    monkeypatch.setattr("app.providers.stackexchange.build_client", lambda *_args, **_kwargs: FakeClient())
    results = asyncio.run(provider.search("FastAPI dependency injection", 1))

    assert calls["url"] == "https://api.stackexchange.com/2.3/search/advanced"
    assert calls["headers"] == {"Accept": "application/json"}
    assert calls["params"]["site"] == "stackoverflow"
    assert calls["params"]["q"] == "FastAPI dependency injection"
    assert calls["params"]["key"] == "se-key"
    assert results[0].title == "How to use FastAPI dependency injection?"
    assert results[0].url == "https://stackoverflow.com/questions/1/how-to-use-fastapi"
    assert "score 12" in results[0].snippet
    assert "3 answers" in results[0].snippet
