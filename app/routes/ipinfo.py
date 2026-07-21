from typing import Any

from fastapi import APIRouter, Depends, Query

from app.config import Settings, get_settings
from app.providers.ipinfo import IpInfoProvider
from app.providers.ipsb import IpSbProvider
from app.schemas.ipinfo import IpInfoResponse
from app.services.cache_service import CacheService
from app.utils.auth import require_api_key
from app.utils.errors import GatewayError

router = APIRouter(tags=["ipinfo"])


@router.get("/ipinfo", response_model=IpInfoResponse)
async def ipinfo(
    ip: str = Query(..., min_length=2, max_length=64),
    _: None = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
) -> IpInfoResponse:
    normalized_ip = IpInfoProvider._normalize_ip(ip)
    providers = _configured_providers(settings)
    if not providers:
        raise GatewayError("IP 查询未配置可用数据源", status_code=503)
    cache = CacheService(settings)
    cache_key = f"ipinfo:v2:{normalized_ip}"
    try:
        cached = await cache.get_json(cache_key)
        if cached is not None:
            provider_name = str(cached.get("provider") or "unknown")
            data = cached.get("data") if isinstance(cached.get("data"), dict) else cached
            return IpInfoResponse(success=True, provider=provider_name, ip=normalized_ip, cached=True, data=data)

        provider_name, data = await _lookup_with_fallback(providers, normalized_ip)
        await cache.set_json(cache_key, {"provider": provider_name, "data": data})
        return IpInfoResponse(success=True, provider=provider_name, ip=normalized_ip, cached=False, data=data)
    finally:
        await cache.close()


def _configured_providers(settings: Settings) -> list[IpInfoProvider | IpSbProvider]:
    providers: list[IpInfoProvider | IpSbProvider] = []
    if settings.ipinfo_enabled and settings.ipinfo_api_key and settings.ipinfo_base_url:
        providers.append(IpInfoProvider(settings))
    if settings.ipsb_enabled and settings.ipsb_base_url:
        providers.append(IpSbProvider(settings))
    return providers


async def _lookup_with_fallback(
    providers: list[IpInfoProvider | IpSbProvider], ip: str
) -> tuple[str, dict[str, Any]]:
    failures: list[GatewayError] = []
    for provider in providers:
        try:
            return provider.name, await provider.lookup(ip)
        except GatewayError as exc:
            if exc.status_code < 500:
                raise
            failures.append(exc)
    if failures:
        raise failures[-1]
    raise GatewayError("IP 查询未配置可用数据源", status_code=503)
