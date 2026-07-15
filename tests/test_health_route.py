import asyncio

from app.config import Settings
from app.routes.health import health


class FakeCache:
    def __init__(self, *_args, **_kwargs):
        pass

    async def ping(self):
        return True

    async def close(self):
        return None


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
