import asyncio

from app.config import Settings
from app.providers.duckduckgo import DuckDuckGoProvider


def test_duckduckgo_provider_parses_instant_answer_results(monkeypatch):
    provider = DuckDuckGoProvider(Settings(gateway_api_key="test"))
    calls = {"url": "", "params": {}, "headers": {}}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "Heading": "Python",
                "AbstractURL": "https://en.wikipedia.org/wiki/Python_(programming_language)",
                "AbstractText": "Python is a programming language.",
                "RelatedTopics": [
                    {
                        "Name": "Languages",
                        "Topics": [
                            {
                                "FirstURL": "https://duckduckgo.com/Python_syntax",
                                "Text": "Python syntax - syntax overview",
                            }
                        ],
                    }
                ],
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, **kwargs):
            calls["url"] = url
            calls["params"] = kwargs.get("params") or {}
            calls["headers"] = kwargs.get("headers") or {}
            return FakeResponse()

    monkeypatch.setattr("app.providers.duckduckgo.build_client", lambda *_args, **_kwargs: FakeClient())
    results = asyncio.run(provider.search("python", 2))

    assert calls["url"] == "https://api.duckduckgo.com"
    assert calls["params"] == {"q": "python", "format": "json", "no_html": "1", "skip_disambig": "1"}
    assert calls["headers"] == {"Accept": "application/json"}
    assert len(results) == 2
    assert results[0].title == "Python"
    assert results[0].url == "https://en.wikipedia.org/wiki/Python_(programming_language)"
    assert results[0].snippet == "Python is a programming language."
    assert results[1].title == "Python syntax"
