import ipaddress
import re
from typing import Any

from app.config import Settings
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call


class IpInfoProvider:
    name = "ipinfo"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def lookup(self, ip: str) -> dict[str, Any]:
        if not self.settings.ipinfo_enabled:
            raise GatewayError("IPInfo 查询未启用", status_code=500)
        if not self.settings.ipinfo_api_key:
            raise GatewayError("IPInfo API Key 未配置", status_code=500)
        if not self.settings.ipinfo_base_url:
            raise GatewayError("IPInfo API base 未配置", status_code=500)
        normalized_ip = self._normalize_ip(ip)

        async def request() -> dict[str, Any]:
            endpoint = self.settings.ipinfo_base_url.rstrip("/") + "/"
            async with build_client(self.settings, timeout=self.settings.ipinfo_timeout_seconds) as client:
                resp = await client.get(
                    endpoint,
                    params={"key": self.settings.ipinfo_api_key, "ip": normalized_ip},
                    follow_redirects=True,
                )
                resp.raise_for_status()
                data = resp.json()
            if not isinstance(data, dict):
                raise GatewayError("IPInfo 返回非对象 JSON", status_code=502)
            return self._with_normalized_review(data, normalized_ip)

        return await timed_call("IPInfo", request)

    @staticmethod
    def _normalize_ip(ip: str) -> str:
        try:
            return str(ipaddress.ip_address(ip.strip()))
        except ValueError as exc:
            raise GatewayError("IP 地址格式无效", status_code=422, detail={"ip": ip}) from exc

    @classmethod
    def _with_normalized_review(cls, data: dict[str, Any], ip: str) -> dict[str, Any]:
        normalized = cls.normalize_ipinfo_response(data, ip)
        result = dict(data)
        result["ip"] = cls._text(data.get("ip"), 80) or ip
        result["normalized"] = normalized
        return result

    @classmethod
    def normalize_ipinfo_response(cls, data: dict[str, Any], ip: str = "") -> dict[str, Any]:
        location = cls._dict(data.get("location")) or data
        network = cls._dict(data.get("network")) or cls._dict(data.get("asn")) or {}
        security = cls._dict(data.get("security")) or cls._dict(data.get("threat")) or {}
        country = data.get("country") or location.get("country") or {}
        country_code = cls._country_code(
            data.get("country_code")
            or cls._nested(country, ["code", "iso_code"])
            if isinstance(country, dict)
            else data.get("country_code") or country
        )
        country_name = cls._text(
            cls._nested(country, ["name"]) if isinstance(country, dict) else data.get("country_name"),
            80,
        )
        region = cls._text(
            cls._nested(location, ["region.name"]) or location.get("region") or data.get("region"),
            120,
        )
        city = cls._text(cls._nested(location, ["city.name"]) or location.get("city") or data.get("city"), 120)
        asn = cls._text(network.get("asn") or network.get("as") or data.get("asn"), 40)
        organization = cls._text(network.get("organization") or network.get("org") or data.get("organization"), 160)
        isp = cls._text(network.get("isp") or data.get("isp") or organization, 160)
        connection_type = cls._text(network.get("type") or data.get("type") or data.get("connection_type"), 80)
        flags = {
            "isVpn": cls._bool(security.get("is_vpn") or security.get("vpn") or data.get("is_vpn") or data.get("vpn")),
            "isProxy": cls._bool(
                security.get("is_proxy") or security.get("proxy") or data.get("is_proxy") or data.get("proxy")
            ),
            "isTor": cls._bool(security.get("is_tor") or security.get("tor") or data.get("is_tor") or data.get("tor")),
            "isThreat": cls._bool(
                security.get("is_threat") or security.get("threat") or data.get("is_threat") or data.get("threat")
            ),
        }
        risk_score = cls._risk_score(flags, connection_type, organization)
        return {
            "ip": ip,
            "maskedIp": cls.mask_ip(ip),
            "countryCode": country_code,
            "countryName": country_name,
            "flag": cls.flag_from_country(country_code),
            "region": region,
            "city": city,
            "asn": asn,
            "organization": organization,
            "isp": isp,
            "connectionType": connection_type,
            **flags,
            "riskScore": risk_score,
            "riskLevel": cls._risk_level(risk_score),
            "riskLabels": cls._risk_labels(flags, connection_type),
        }

    @staticmethod
    def mask_ip(ip: str) -> str:
        try:
            parsed = ipaddress.ip_address(ip.strip())
        except ValueError:
            return ""
        if isinstance(parsed, ipaddress.IPv4Address):
            parts = str(parsed).split(".")
            return ".".join([parts[0], parts[1], parts[2], "x"])
        hextets = parsed.exploded.split(":")
        return ":".join(hextets[:4] + ["xxxx", "xxxx", "xxxx", "xxxx"])

    @staticmethod
    def flag_from_country(country_code: str) -> str:
        if not re.fullmatch(r"[A-Z]{2}", country_code or ""):
            return ""
        base = 127397
        try:
            return "".join(chr(base + ord(char)) for char in country_code)
        except ValueError:
            return ""

    @staticmethod
    def _risk_score(flags: dict[str, bool], connection_type: str, organization: str) -> int:
        score = 0
        if flags["isThreat"]:
            score += 80
        if flags["isTor"]:
            score += 70
        if flags["isProxy"]:
            score += 45
        if flags["isVpn"]:
            score += 35
        text = f"{connection_type} {organization}".lower()
        if any(keyword in text for keyword in ("hosting", "datacenter", "data center", "cloud", "vps")):
            score += 15
        return min(score, 100)

    @staticmethod
    def _risk_level(score: int) -> str:
        if score >= 70:
            return "high"
        if score >= 35:
            return "medium"
        if score > 0:
            return "low"
        return "normal"

    @staticmethod
    def _risk_labels(flags: dict[str, bool], connection_type: str) -> list[str]:
        labels = []
        if flags["isVpn"]:
            labels.append("VPN")
        if flags["isProxy"]:
            labels.append("Proxy")
        if flags["isTor"]:
            labels.append("Tor")
        if flags["isThreat"]:
            labels.append("Threat")
        if connection_type:
            labels.append(connection_type)
        return labels

    @staticmethod
    def _country_code(value: Any) -> str:
        text = IpInfoProvider._text(value, 12).upper()
        if text in {"", "UN", "XX", "ZZ", "T1", "A1", "A2", "O1"}:
            return ""
        return text if re.fullmatch(r"[A-Z]{2}", text) else ""

    @staticmethod
    def _dict(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _nested(value: Any, keys: list[str]) -> Any:
        if not isinstance(value, dict):
            return ""
        for key in keys:
            current: Any = value
            for part in key.split("."):
                current = current.get(part) if isinstance(current, dict) else None
            if current not in (None, ""):
                return current
        return ""

    @staticmethod
    def _text(value: Any, max_length: int) -> str:
        if isinstance(value, dict):
            return ""
        return re.sub(r"\s+", " ", str(value or "")).strip()[:max_length]

    @staticmethod
    def _bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value == 1
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes"}
        return False
