import asyncio

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.config import get_settings
from app.main import app
from app.routes.health import health


class FakeCache:
    def __init__(self, *_args, **_kwargs):
        pass

    async def ping(self):
        return True

    async def close(self):
        return None


def test_healthz_remains_dependency_free_liveness():
    response = TestClient(app).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"success": True}


def test_readyz_reports_ready_when_required_dependencies_are_available(monkeypatch):
    monkeypatch.setattr("app.services.readiness_service.CacheService", FakeCache)
    app.dependency_overrides[get_settings] = lambda: Settings(gateway_api_key="test")

    try:
        response = TestClient(app).get("/readyz", headers={"X-API-Key": "test"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "status": "ready",
        "checks": {
            "redis": {"status": "ok", "required": True, "configured": True},
            "searxng": {"status": "disabled", "required": False, "configured": False},
            "groksearch_bridge": {"status": "disabled", "required": False, "configured": False},
        },
    }


def test_readyz_checks_enabled_internal_http_dependencies(monkeypatch):
    requested_urls = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(200, json={"success": True})

    def fake_build_client(*_args, **_kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(handle_request))

    monkeypatch.setattr("app.services.readiness_service.CacheService", FakeCache)
    monkeypatch.setattr("app.services.readiness_service.build_client", fake_build_client, raising=False)
    app.dependency_overrides[get_settings] = lambda: Settings(
        gateway_api_key="test",
        searxng_enabled=True,
        searxng_base_url="http://searx.internal:8080",
        grok_search_enabled=True,
        grok_backend="groksearch",
        groksearch_bridge_url="http://bridge.internal:8010",
    )

    try:
        response = TestClient(app).get("/readyz", headers={"X-API-Key": "test"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["checks"] == {
        "redis": {"status": "ok", "required": True, "configured": True},
        "searxng": {"status": "ok", "required": True, "configured": True},
        "groksearch_bridge": {"status": "ok", "required": True, "configured": True},
    }
    assert requested_urls == [
        "http://searx.internal:8080/healthz",
        "http://bridge.internal:8010/healthz",
    ]


def test_readyz_reports_missing_redis_configuration_without_crashing():
    app.dependency_overrides[get_settings] = lambda: Settings(
        gateway_api_key="gateway-secret",
        redis_url="",
    )

    try:
        response = TestClient(app, raise_server_exceptions=False).get(
            "/readyz", headers={"X-API-Key": "gateway-secret"}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {
        "success": False,
        "status": "not_ready",
        "checks": {
            "redis": {"status": "misconfigured", "required": True, "configured": False},
            "searxng": {"status": "disabled", "required": False, "configured": False},
            "groksearch_bridge": {"status": "disabled", "required": False, "configured": False},
        },
    }
    assert "gateway-secret" not in response.text


def test_readyz_reports_invalid_internal_url_as_misconfigured(monkeypatch):
    monkeypatch.setattr("app.services.readiness_service.CacheService", FakeCache)
    app.dependency_overrides[get_settings] = lambda: Settings(
        gateway_api_key="gateway-secret",
        searxng_enabled=True,
        searxng_base_url="searx-secret@internal:8080",
    )

    try:
        response = TestClient(app).get(
            "/readyz", headers={"X-API-Key": "gateway-secret"}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["checks"]["searxng"] == {
        "status": "misconfigured",
        "required": True,
        "configured": False,
    }
    assert "searx-secret" not in response.text


def test_readyz_returns_sanitized_503_when_internal_dependency_is_unavailable(monkeypatch):
    def handle_request(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream-secret-detail")

    def fake_build_client(*_args, **_kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(handle_request))

    monkeypatch.setattr("app.services.readiness_service.CacheService", FakeCache)
    monkeypatch.setattr("app.services.readiness_service.build_client", fake_build_client)
    app.dependency_overrides[get_settings] = lambda: Settings(
        gateway_api_key="gateway-secret",
        searxng_enabled=True,
        searxng_base_url="http://searx.internal:8080",
    )

    try:
        response = TestClient(app).get(
            "/readyz", headers={"X-API-Key": "gateway-secret"}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["checks"]["searxng"] == {
        "status": "unavailable",
        "required": True,
        "configured": True,
    }
    assert "gateway-secret" not in response.text
    assert "upstream-secret-detail" not in response.text


def test_health_reports_screenshot_upstream_count(monkeypatch):
    monkeypatch.setattr("app.routes.health.CacheService", FakeCache)
    response = asyncio.run(
        health(
            settings=Settings(
                gateway_api_key="test",
                snapapi_api_key="snap",
                apiflash_access_key="flash",
                screenshot_provider_order="snapapi,apiflash,thumbnailws",
            )
        )
    )

    assert response.providers["screenshot"].configured is True
    assert response.providers["screenshot"].upstreams == 2


def test_health_reports_grok_hybrid_backend_count(monkeypatch):
    monkeypatch.setattr("app.routes.health.CacheService", FakeCache)
    response = asyncio.run(
        health(
            settings=Settings(
                gateway_api_key="test",
                grok_search_enabled=True,
                grok_backend="hybrid",
                grok_api_key="gk",
                grok_base_url="https://grok.example/v1",
                groksearch_bridge_url="http://bridge:8010",
            )
        )
    )

    assert response.providers["grok"].configured is True
    assert response.providers["grok"].upstreams == 2


def test_health_reports_fixed_answer_api_without_exposing_endpoint(monkeypatch):
    monkeypatch.setattr("app.routes.health.CacheService", FakeCache)
    response = asyncio.run(
        health(
            settings=Settings(
                gateway_api_key="test",
                answer_api_base_url="https://fixed.example/v1",
                answer_api_model="fixed-model",
            )
        )
    )

    assert response.providers["answer_api"].configured is True
    assert response.providers["answer_api"].model == "fixed-model"
    assert "fixed.example" not in response.model_dump_json()


def test_health_reports_zhihu_without_exposing_key(monkeypatch):
    monkeypatch.setattr("app.routes.health.CacheService", FakeCache)
    response = asyncio.run(
        health(settings=Settings(gateway_api_key="test", zhihu_api_key="zhihu-secret"))
    )

    assert response.providers["zhihu"].configured is True
    assert "zhihu-secret" not in response.model_dump_json()


def test_health_reports_serpjet_key_count_without_exposing_keys(monkeypatch):
    monkeypatch.setattr("app.routes.health.CacheService", FakeCache)
    response = asyncio.run(
        health(settings=Settings(gateway_api_key="test", serpjet_api_keys="first-secret,second-secret"))
    )

    assert response.providers["serpjet"].configured is True
    assert response.providers["serpjet"].upstreams == 2
    assert "first-secret" not in response.model_dump_json()
    assert "second-secret" not in response.model_dump_json()
