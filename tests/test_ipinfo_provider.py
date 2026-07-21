import asyncio

import httpx
import pytest

from app.config import Settings
from app.providers.ipsb import IpSbProvider
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


def test_ipsb_provider_maps_geoip_without_claiming_risk_signals(monkeypatch):
    provider = IpSbProvider(
        Settings(
            gateway_api_key="test",
            ipsb_enabled=True,
            ipsb_base_url="https://api.ip.sb/geoip/",
        )
    )
    calls = {"url": "", "headers": {}}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "ip": "1.1.1.1",
                "country_code": "US",
                "country": "United States",
                "region": "California",
                "city": "Los Angeles",
                "asn": 13335,
                "asn_organization": "Cloudflare, Inc.",
                "isp": "Cloudflare",
                "organization": "Cloudflare",
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, **kwargs):
            calls["url"] = url
            calls["headers"] = kwargs.get("headers") or {}
            return FakeResponse()

    monkeypatch.setattr("app.providers.ipsb.build_client", lambda *_args, **_kwargs: FakeClient())
    result = asyncio.run(provider.lookup("1.1.1.1"))

    assert calls["url"] == "https://api.ip.sb/geoip/1.1.1.1"
    assert calls["headers"]["User-Agent"].startswith("AI-Search-Gateway/")
    assert result["ip"] == "1.1.1.1"
    assert result["normalized"]["countryCode"] == "US"
    assert result["normalized"]["asn"] == "AS13335"
    assert result["normalized"]["organization"] == "Cloudflare, Inc."
    assert result["normalized"]["isVpn"] is None
    assert result["normalized"]["isProxy"] is None
    assert result["normalized"]["isTor"] is None
    assert result["normalized"]["isThreat"] is None
    assert result["normalized"]["riskScore"] is None
    assert result["normalized"]["riskLevel"] == "unknown"


@pytest.mark.parametrize("failure", ["status", "timeout", "json", "mismatch"])
def test_ipsb_provider_fails_loudly_for_unusable_responses(monkeypatch, failure):
    provider = IpSbProvider(Settings(gateway_api_key="test", ipsb_enabled=True))

    class FakeResponse:
        def raise_for_status(self) -> None:
            if failure == "status":
                request = httpx.Request("GET", "https://api.ip.sb/geoip/1.1.1.1")
                response = httpx.Response(429, request=request)
                raise httpx.HTTPStatusError("limited", request=request, response=response)

        def json(self):
            if failure == "json":
                return ["not", "an", "object"]
            if failure == "mismatch":
                return {"ip": "8.8.8.8"}
            return {"ip": "1.1.1.1"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, _url, **_kwargs):
            if failure == "timeout":
                raise httpx.ReadTimeout("timed out")
            return FakeResponse()

    monkeypatch.setattr("app.providers.ipsb.build_client", lambda *_args, **_kwargs: FakeClient())

    with pytest.raises(GatewayError) as caught:
        asyncio.run(provider.lookup("1.1.1.1"))

    if failure == "timeout":
        assert caught.value.status_code == 504
    else:
        assert caught.value.status_code == 502
