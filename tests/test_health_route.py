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
