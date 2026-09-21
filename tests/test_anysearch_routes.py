from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app
from app.schemas.common import SearchResult
from app.schemas.search import SearchResponse


def test_anysearch_sub_domains_route(monkeypatch):
    async def fake_sub_domains(_self, domains):
        assert domains == ["finance", "security"]
        return [{"domain": "finance"}, {"domain": "security"}]

    monkeypatch.setattr("app.routes.anysearch.AnySearchProvider.sub_domains", fake_sub_domains)
    app.dependency_overrides[get_settings] = lambda: Settings(gateway_api_key="test", anysearch_enabled=True)
    try:
        response = TestClient(app).get(
            "/api/anysearch/sub-domains?domain=finance&domain=security",
            headers={"X-API-Key": "test"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "provider": "anysearch",
        "domains": [{"domain": "finance"}, {"domain": "security"}],
    }


def test_anysearch_batch_route_is_bounded_and_preserves_items(monkeypatch):
    class FakeRouter:
        def __init__(self, _settings):
            pass

        async def search(self, query, provider, max_results, provider_options):
            return SearchResponse(
                success=True,
                provider=provider,
                query=query,
                cached=False,
                results=[SearchResult(title=query, url="https://example.com", snippet=str(provider_options))],
            )

        async def close(self):
            return None

    monkeypatch.setattr("app.routes.anysearch.RouterService", FakeRouter)
    app.dependency_overrides[get_settings] = lambda: Settings(gateway_api_key="test", anysearch_enabled=True)
    try:
        response = TestClient(app).post(
            "/api/anysearch/batch-search",
            headers={"X-API-Key": "test"},
            json={
                "queries": [
                    {
                        "query": "AAPL",
                        "tag": "finance.quote",
                        "params": {"symbol": "AAPL"},
                        "max_results": 2,
                    },
                    {"query": "CVE-2026", "tag": "security.vulnerability"},
                ]
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "anysearch"
    assert [item["query"] for item in payload["results"]] == ["AAPL", "CVE-2026"]


def test_anysearch_batch_route_rejects_more_than_five_queries():
    app.dependency_overrides[get_settings] = lambda: Settings(gateway_api_key="test", anysearch_enabled=True)
    try:
        response = TestClient(app).post(
            "/api/anysearch/batch-search",
            headers={"X-API-Key": "test"},
            json={"queries": [{"query": str(index)} for index in range(6)]},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
