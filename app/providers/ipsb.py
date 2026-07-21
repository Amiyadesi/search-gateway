import ipaddress
from typing import Any

from app.config import Settings
from app.providers.ipinfo import IpInfoProvider
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call


class IpSbProvider:
    name = "ipsb"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def lookup(self, ip: str) -> dict[str, Any]:
        if not self.settings.ipsb_enabled:
            raise GatewayError("IP.SB 查询未启用", status_code=500)
        if not self.settings.ipsb_base_url:
            raise GatewayError("IP.SB API base 未配置", status_code=500)
        normalized_ip = IpInfoProvider._normalize_ip(ip)

        async def request() -> dict[str, Any]:
            endpoint = f"{self.settings.ipsb_base_url}/{normalized_ip}"
            headers = {
                "Accept": "application/json",
                "User-Agent": self.settings.open_data_user_agent.strip() or "AI-Search-Gateway/1.2",
            }
            async with build_client(self.settings, timeout=self.settings.ipsb_timeout_seconds) as client:
                response = await client.get(endpoint, headers=headers)
                response.raise_for_status()
                data = response.json()
            if not isinstance(data, dict):
                raise GatewayError("IP.SB 返回非对象 JSON", status_code=502)
            response_ip = self._validated_response_ip(data.get("ip"), normalized_ip)
            return self._with_normalized_geo(data, response_ip)

        return await timed_call("IP.SB", request)

    @staticmethod
    def _validated_response_ip(value: Any, requested_ip: str) -> str:
        try:
            response_ip = str(ipaddress.ip_address(str(value or "").strip()))
        except ValueError as exc:
            raise GatewayError("IP.SB 返回无效 IP", status_code=502) from exc
        if response_ip != requested_ip:
            raise GatewayError("IP.SB 返回的 IP 与请求不一致", status_code=502)
        return response_ip

    @staticmethod
    def _with_normalized_geo(data: dict[str, Any], ip: str) -> dict[str, Any]:
        source = dict(data)
        source["country_name"] = source.get("country")
        source["organization"] = source.get("asn_organization") or source.get("organization")
        asn = source.get("asn")
        if asn not in (None, ""):
            asn_text = str(asn).strip().upper()
            source["asn"] = asn_text if asn_text.startswith("AS") else f"AS{asn_text}"

        normalized = IpInfoProvider.normalize_ipinfo_response(source, ip)
        normalized.update(
            {
                "isVpn": None,
                "isProxy": None,
                "isTor": None,
                "isThreat": None,
                "riskScore": None,
                "riskLevel": "unknown",
                "riskLabels": [],
            }
        )
        result = dict(data)
        result["ip"] = ip
        result["normalized"] = normalized
        return result
