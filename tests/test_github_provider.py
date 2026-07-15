import asyncio

from app.config import Settings
from app.providers.github import GitHubProvider


def test_github_provider_parses_repository_results_with_optional_token(monkeypatch):
    provider = GitHubProvider(Settings(gateway_api_key="test", github_token="ghp_test"))
    calls = {"url": "", "headers": {}, "params": {}}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "items": [
                    {
                        "full_name": "tiangolo/fastapi",
                        "html_url": "https://github.com/tiangolo/fastapi",
                        "description": "FastAPI framework",
                        "language": "Python",
                        "stargazers_count": 90000,
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

    monkeypatch.setattr("app.providers.github.build_client", lambda *_args, **_kwargs: FakeClient())
    results = asyncio.run(provider.search("fastapi", 1))

    assert calls["url"] == "https://api.github.com/search/repositories"
    assert calls["headers"]["Authorization"] == "Bearer ghp_test"
    assert calls["headers"]["Accept"] == "application/vnd.github+json"
    assert calls["params"] == {"q": "fastapi", "per_page": 1}
    assert results[0].title == "tiangolo/fastapi"
    assert results[0].url == "https://github.com/tiangolo/fastapi"
    assert "FastAPI framework" in results[0].snippet
    assert "90000 stars" in results[0].snippet
