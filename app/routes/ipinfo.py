from fastapi import APIRouter, Depends, Query

from app.config import Settings, get_settings
from app.providers.ipinfo import IpInfoProvider
from app.schemas.ipinfo import IpInfoResponse
from app.services.cache_service import CacheService
from app.utils.auth import require_api_key

router = APIRouter(tags=["ipinfo"])


@router.get("/ipinfo", response_model=IpInfoResponse)
async def ipinfo(
    ip: str = Query(..., min_length=2, max_length=64),
    _: None = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
) -> IpInfoResponse:
    provider = IpInfoProvider(settings)
    normalized_ip = provider._normalize_ip(ip)
    cache = CacheService(settings)
    cache_key = f"ipinfo:{normalized_ip}"
    try:
        cached = await cache.get_json(cache_key)
        if cached is not None:
            return IpInfoResponse(success=True, provider=provider.name, ip=normalized_ip, cached=True, data=cached)

        data = await provider.lookup(normalized_ip)
        await cache.set_json(cache_key, data)
        return IpInfoResponse(success=True, provider=provider.name, ip=normalized_ip, cached=False, data=data)
    finally:
        await cache.close()
