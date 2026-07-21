from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app
from app.utils.errors import GatewayError


class FakeCache:
    value = None

    def __init__(self, *_args, **_kwargs):
        pass

    async def get_json(self, _key):
        return self.value

    async def set_json(self, _key, value):
        self.value = value

    async def close(self):
        return None


def test_ipinfo_uses_ipsb_when_primary_provider_is_not_configured(monkeypatch):
    async def fake_lookup(_self, ip):
        return {"ip": ip, "normalized": {"riskLevel": "unknown"}}

    monkeypatch.setattr("app.routes.ipinfo.CacheService", FakeCache)
    monkeypatch.setattr("app.routes.ipinfo.IpSbProvider.lookup", fake_lookup)
    app.dependency_overrides[get_settings] = lambda: Settings(
        gateway_api_key="test-key",
        ipinfo_enabled=False,
        ipsb_enabled=True,
    )

    try:
        response = TestClient(app).get(
            "/ipinfo",
            params={"ip": "1.1.1.1"},
            headers={"X-API-Key": "test-key"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "provider": "ipsb",
        "ip": "1.1.1.1",
        "cached": False,
        "data": {"ip": "1.1.1.1", "normalized": {"riskLevel": "unknown"}},
    }


def test_ipinfo_falls_back_to_ipsb_after_primary_upstream_failure(monkeypatch):
    calls = []

    async def failed_primary(_self, _ip):
        calls.append("ipinfo")
        raise GatewayError("IPInfo 调用失败", status_code=502)

    async def successful_fallback(_self, ip):
        calls.append("ipsb")
        return {"ip": ip, "normalized": {"riskLevel": "unknown"}}

    FakeCache.value = None
    monkeypatch.setattr("app.routes.ipinfo.CacheService", FakeCache)
    monkeypatch.setattr("app.routes.ipinfo.IpInfoProvider.lookup", failed_primary)
    monkeypatch.setattr("app.routes.ipinfo.IpSbProvider.lookup", successful_fallback)
    app.dependency_overrides[get_settings] = lambda: Settings(
        gateway_api_key="test-key",
        ipinfo_enabled=True,
        ipinfo_api_key="primary-secret",
        ipinfo_base_url="https://primary.example/api",
        ipsb_enabled=True,
    )

    try:
        response = TestClient(app).get(
            "/ipinfo",
            params={"ip": "8.8.8.8"},
            headers={"X-API-Key": "test-key"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["provider"] == "ipsb"
    assert calls == ["ipinfo", "ipsb"]


def test_ipinfo_rejects_invalid_ip_without_calling_any_upstream(monkeypatch):
    calls = []

    async def unexpected_lookup(_self, _ip):
        calls.append("called")
        return {}

    monkeypatch.setattr("app.routes.ipinfo.CacheService", FakeCache)
    monkeypatch.setattr("app.routes.ipinfo.IpSbProvider.lookup", unexpected_lookup)
    app.dependency_overrides[get_settings] = lambda: Settings(
        gateway_api_key="test-key",
        ipsb_enabled=True,
    )

    try:
        response = TestClient(app).get(
            "/ipinfo",
            params={"ip": "not-an-ip"},
            headers={"X-API-Key": "test-key"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["error"] == "IP 地址格式无效"
    assert calls == []
