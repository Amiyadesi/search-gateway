import asyncio

from app.config import Settings
from app.providers.ipinfo import IpInfoProvider
from app.utils.errors import GatewayError


def test_ipinfo_provider_calls_api_with_key_and_ip(monkeypatch):
    provider = IpInfoProvider(
        Settings(
            gateway_api_key="test",
            ipinfo_enabled=True,
            ipinfo_api_key="ik",
            ipinfo_base_url="https://ipinfo.example/api/",
        )
    )
    calls = {"url": "", "params": {}}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "ip": "8.8.8.8",
                "location": {"country": {"code": "US", "name": "United States"}, "region": "California", "city": "Mountain View"},
                "network": {"asn": "AS15169", "organization": "Google LLC", "type": "hosting"},
                "security": {"is_vpn": True, "is_proxy": False, "is_tor": False, "is_threat": False},
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, **kwargs):
            calls["url"] = url
            calls["params"] = kwargs.get("params") or {}
            return FakeResponse()

    monkeypatch.setattr("app.providers.ipinfo.build_client", lambda *_args, **_kwargs: FakeClient())
    result = asyncio.run(provider.lookup("8.8.8.8"))

    assert calls["url"] == "https://ipinfo.example/api/"
    assert calls["params"] == {"key": "ik", "ip": "8.8.8.8"}
    assert result["ip"] == "8.8.8.8"
    assert result["normalized"]["maskedIp"] == "8.8.8.x"
    assert result["normalized"]["countryCode"] == "US"
    assert result["normalized"]["flag"] == "🇺🇸"
    assert result["normalized"]["isVpn"] is True
    assert result["normalized"]["riskLevel"] == "medium"
    assert "VPN" in result["normalized"]["riskLabels"]


def test_ipinfo_provider_rejects_invalid_ip():
    provider = IpInfoProvider(
        Settings(
            gateway_api_key="test",
            ipinfo_enabled=True,
            ipinfo_api_key="ik",
            ipinfo_base_url="https://ipinfo.example/api",
        )
    )

    try:
        asyncio.run(provider.lookup("not-an-ip"))
    except GatewayError as exc:
        assert exc.status_code == 422
        assert "IP 地址格式无效" in exc.message
    else:
        raise AssertionError("expected GatewayError")


def test_ipinfo_provider_normalizes_common_flat_response():
    normalized = IpInfoProvider.normalize_ipinfo_response(
        {
            "country_code": "JP",
            "country_name": "Japan",
            "region": "Tokyo",
            "city": "Chiyoda",
            "asn": "AS2516",
            "organization": "KDDI",
            "proxy": True,
        },
        "2001:db8::1",
    )

    assert normalized["maskedIp"].startswith("2001:0db8:0000:0000")
    assert normalized["countryCode"] == "JP"
    assert normalized["flag"] == "🇯🇵"
    assert normalized["isProxy"] is True
    assert normalized["riskScore"] >= 35
    assert "Proxy" in normalized["riskLabels"]
