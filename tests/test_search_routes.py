from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app
from app.schemas.common import SearchResult
from app.schemas.search import SearchResponse


class FakeRouter:
    calls: list[dict] = []

    def __init__(self, _settings):
        pass

    async def search(self, query, provider, max_results, provider_options):
        self.calls.append(
            {
                "query": query,
                "provider": provider,
                "max_results": max_results,
                "provider_options": provider_options,
            }
        )
        return SearchResponse(
            success=True,
            provider=provider,
            query=query,
            cached=False,
            results=[SearchResult(title=query, url="https://example.com", snippet="ok")],
        )

    async def close(self):
        return None


def test_search_auto_does_not_implicitly_route_anysearch(monkeypatch):
    FakeRouter.calls = []
    monkeypatch.setattr("app.routes.search.RouterService", FakeRouter)
    app.dependency_overrides[get_settings] = lambda: Settings(gateway_api_key="test")
    try:
        response = TestClient(app).get(
            "/api/search",
            params={"q": "FastAPI latest release", "provider": "auto", "zone": "intl"},
            headers={"X-API-Key": "test"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert "不会隐式改写 provider" in response.json()["error"]
    assert FakeRouter.calls == []


def test_search_explicit_anysearch_receives_vertical_options(monkeypatch):
    FakeRouter.calls = []
    monkeypatch.setattr("app.routes.search.RouterService", FakeRouter)
    app.dependency_overrides[get_settings] = lambda: Settings(gateway_api_key="test")
    try:
        response = TestClient(app).get(
            "/api/search",
            params={
                "q": "AAPL",
                "provider": "anysearch",
                "tag": "finance.quote",
                "domain": "finance",
                "params": '{"symbol":"AAPL"}',
                "zone": "intl",
                "language": "en",
            },
            headers={"X-API-Key": "test"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert FakeRouter.calls == [
        {
            "query": "AAPL",
            "provider": "anysearch",
            "max_results": 5,
            "provider_options": {
                "tag": "finance.quote",
                "params": {"symbol": "AAPL"},
                "zone": "intl",
                "language": "en",
            },
        }
    ]
