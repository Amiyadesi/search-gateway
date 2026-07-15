import base64
import re

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from app.config import Settings, get_settings
from app.schemas.screenshot import ScreenshotRequest, ScreenshotResponse
from app.services.screenshot_service import ScreenshotService
from app.utils.auth import require_api_key
from app.utils.errors import GatewayError

router = APIRouter(tags=["screenshot"])


@router.post("/screenshot", response_model=ScreenshotResponse)
async def screenshot(
    payload: ScreenshotRequest,
    _: None = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
) -> ScreenshotResponse:
    service = ScreenshotService(settings)
    try:
        metadata = await service.capture(payload)
        return ScreenshotResponse(success=not metadata.degraded, screenshot=metadata)
    finally:
        await service.close()


@router.get("/screenshot-cache/{cache_id}")
async def screenshot_cache(
    cache_id: str,
    _: None = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
) -> Response:
    if not re.fullmatch(r"screenshot:[a-f0-9]{64}", cache_id):
        raise GatewayError("截图缓存 ID 无效", status_code=422)
    service = ScreenshotService(settings)
    try:
        entry = await service.get_cached(cache_id)
    finally:
        await service.close()
    if not entry:
        raise GatewayError("截图缓存不存在或已过期", status_code=404)
    return Response(
        content=base64.b64decode(entry.content_base64),
        media_type=entry.content_type,
        headers={"Cache-Control": f"private, max-age={settings.screenshot_cache_ttl_seconds}"},
    )
