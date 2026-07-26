from fastapi import APIRouter, Depends

from app.config import Settings, get_settings
from app.providers.firecrawl import FirecrawlProvider
from app.schemas.extract import ExtractRequest, ExtractResponse
from app.schemas.screenshot import ScreenshotRequest
from app.services.screenshot_service import ScreenshotService
from app.utils.auth import require_api_key
from app.utils.errors import GatewayError
from app.utils.logging import logger
from app.utils.markdown_quality import assess_markdown_quality

router = APIRouter(tags=["extract"])


@router.post("/extract", response_model=ExtractResponse)
async def extract(
    payload: ExtractRequest,
    _: None = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
) -> ExtractResponse:
    url = str(payload.url)
    provider = FirecrawlProvider(settings)
    markdown = ""
    degraded = False
    error = None
    try:
        markdown = await provider.extract(url)
    except GatewayError as exc:
        if payload.screenshot_mode == "never":
            raise
        degraded = True
        error = exc.message
        logger.warning("Firecrawl 提取失败，尝试截图兜底: {}", exc.message)

    quality = assess_markdown_quality(markdown, settings.screenshot_min_markdown_chars)
    if quality.status != "usable":
        degraded = True
        if error is None:
            error = "页面缺少可读正文" if quality.status == "non_text" else "页面正文质量不足"

    screenshot = None
    should_capture = payload.screenshot_mode == "force" or (
        payload.screenshot_mode == "auto"
        and quality.status != "usable"
    )
    if should_capture:
        service = ScreenshotService(settings)
        try:
            screenshot = await service.capture(ScreenshotRequest(url=payload.url))
            if not markdown:
                markdown = (
                    f"截图兜底：页面正文提取不可用。截图缓存：{screenshot.image_url or 'unavailable'}，"
                    f"provider={screenshot.provider}。"
                )
        finally:
            await service.close()

    return ExtractResponse(
        success=quality.status != "non_text",
        markdown=markdown,
        quality=quality.status,
        readable_chars=quality.readable_chars,
        screenshot=screenshot,
        degraded=degraded or bool(screenshot and screenshot.degraded),
        error=error,
    )
