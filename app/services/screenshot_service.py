import base64
import hashlib
import ipaddress
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import Settings
from app.schemas.screenshot import (
    SCREENSHOT_PROVIDERS,
    ScreenshotCacheEntry,
    ScreenshotMetadata,
    ScreenshotRequest,
)
from app.services.cache_service import CacheService
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call
from app.utils.logging import logger


@dataclass(frozen=True)
class ScreenshotCapture:
    provider: str
    content: bytes
    content_type: str


class ScreenshotService:
    """统一截图上游，返回网关缓存 URL，避免泄露第三方带密钥 URL。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.cache = CacheService(settings)

    async def capture(self, request: ScreenshotRequest) -> ScreenshotMetadata:
        url = str(request.url)
        self._validate_target_url(url)
        cache_key = self._cache_key(request)
        cached = await self.cache.get_json(cache_key)
        if cached:
            entry = ScreenshotCacheEntry(**cached)
            return ScreenshotMetadata(
                provider=entry.provider,
                cache_id=cache_key,
                image_url=f"/screenshot-cache/{cache_key}",
                content_type=entry.content_type,
                width=entry.width,
                height=entry.height,
                cached=True,
            )

        last_error = ""
        for provider in self._provider_order(request.provider):
            try:
                capture = await self._capture_with_provider(provider, request)
                if len(capture.content) > self.settings.screenshot_cache_max_bytes:
                    return ScreenshotMetadata(
                        provider=capture.provider,
                        width=request.width,
                        height=request.height,
                        degraded=True,
                        error="截图超过缓存大小限制，未保存。",
                    )
                await self._store(cache_key, capture, request)
                return ScreenshotMetadata(
                    provider=capture.provider,
                    cache_id=cache_key,
                    image_url=f"/screenshot-cache/{cache_key}",
                    content_type=capture.content_type,
                    width=request.width,
                    height=request.height,
                )
            except Exception as exc:
                last_error = self._safe_error(exc)
                logger.warning("截图 provider {} 失败，尝试下一个: {}", provider, last_error)
                if request.provider != "auto":
                    break

        return ScreenshotMetadata(provider=request.provider, degraded=True, error=last_error or "截图上游不可用")

    async def get_cached(self, cache_id: str) -> ScreenshotCacheEntry | None:
        data = await self.cache.get_json(cache_id)
        if not data:
            return None
        return ScreenshotCacheEntry(**data)

    async def close(self) -> None:
        await self.cache.close()

    async def _store(self, cache_key: str, capture: ScreenshotCapture, request: ScreenshotRequest) -> None:
        entry = ScreenshotCacheEntry(
            content_base64=base64.b64encode(capture.content).decode("ascii"),
            content_type=capture.content_type,
            provider=capture.provider,
            width=request.width,
            height=request.height,
        )
        await self.cache.set_json(
            cache_key,
            entry.model_dump(mode="json"),
            ttl=self.settings.screenshot_cache_ttl_seconds,
        )

    async def _capture_with_provider(self, provider: str, request: ScreenshotRequest) -> ScreenshotCapture:
        configured = self._is_configured(provider)
        if not configured:
            raise GatewayError(f"{provider} 截图上游未配置", status_code=500)

        async def do_request() -> ScreenshotCapture:
            async with build_client(self.settings, timeout=self.settings.screenshot_timeout_seconds) as client:
                response = await self._request_provider(client, provider, request)
                return await self._capture_from_response(client, provider, response)

        return await timed_call(f"Screenshot:{provider}", do_request)

    async def _request_provider(
        self,
        client: httpx.AsyncClient,
        provider: str,
        request: ScreenshotRequest,
    ) -> httpx.Response:
        url = str(request.url)
        width = request.width
        height = request.height
        fmt = "jpg" if request.format == "jpeg" else request.format
        if provider == "apiflash":
            return await client.get(
                self.settings.apiflash_base_url,
                params={
                    "access_key": self.settings.apiflash_access_key,
                    "url": url,
                    "width": width,
                    "height": height,
                    "format": fmt,
                    "full_page": str(request.full_page).lower(),
                    "wait_until": request.wait_until,
                    "delay": int(request.delay_ms / 1000),
                },
            )
        if provider == "microlink":
            params: dict[str, Any] = {
                "url": url,
                "screenshot": "true",
                "meta": "false",
                "embed": "screenshot.url",
                "viewport.width": width,
                "viewport.height": height,
            }
            if self.settings.microlink_api_key:
                params["apiKey"] = self.settings.microlink_api_key
            return await client.get(self.settings.microlink_base_url, params=params)
        if provider == "screenshotlayer":
            return await client.get(
                self.settings.screenshotlayer_base_url,
                params={
                    "access_key": self.settings.screenshotlayer_access_key,
                    "url": url,
                    "viewport": f"{width}x{height}",
                    "fullpage": 1 if request.full_page else 0,
                    "format": fmt,
                },
            )
        if provider == "screenshotmachine":
            return await client.get(
                self.settings.screenshotmachine_base_url,
                params={
                    "key": self.settings.screenshotmachine_key,
                    "url": url,
                    "dimension": f"{width}x{'full' if request.full_page else height}",
                    "format": fmt,
                    "cacheLimit": "0",
                    "delay": request.delay_ms,
                },
            )
        if provider == "phantomjscloud":
            payload = {
                "url": url,
                "renderType": fmt,
                "outputAsJson": False,
                "requestSettings": {"waitInterval": request.delay_ms},
                "viewportSettings": {"width": width, "height": height},
            }
            return await client.post(
                f"{self.settings.phantomjscloud_base_url}/{self.settings.phantomjscloud_api_key}/",
                json=payload,
            )
        if provider == "snapapi":
            return await client.post(
                self.settings.snapapi_base_url,
                headers={"X-Api-Key": self.settings.snapapi_api_key},
                json={
                    "url": url,
                    "width": width,
                    "height": height,
                    "full_page": request.full_page,
                    "format": fmt,
                    "delay": request.delay_ms,
                },
            )
        if provider == "screenshotbase":
            return await client.get(
                self.settings.screenshotbase_base_url,
                headers={"apikey": self.settings.screenshotbase_api_key},
                params={
                    "url": url,
                    "viewport_width": width,
                    "viewport_height": height,
                    "full_page": 1 if request.full_page else 0,
                    "format": fmt,
                    "delay": self._delay_seconds(request.delay_ms),
                    "wait_until": self._playwright_wait_until(request.wait_until),
                },
            )
        if provider == "screenshotscout":
            return await client.post(
                self.settings.screenshotscout_base_url,
                json={
                    "access_key": self.settings.screenshotscout_access_key,
                    "url": url,
                    "device_viewport_width": width,
                    "device_viewport_height": height,
                    "full_page": request.full_page,
                    "format": fmt,
                    "wait_until": self._playwright_wait_until(request.wait_until),
                    "delay": self._delay_seconds(request.delay_ms),
                },
            )
        if provider == "thumbnailws":
            return await client.get(
                f"{self.settings.thumbnail_ws_base_url}/{self.settings.thumbnail_ws_api_key}/thumbnail/get",
                params={
                    "url": url,
                    "width": width,
                    "delay": min(request.delay_ms, 5000),
                    "fullpage": str(request.full_page).lower(),
                },
            )
        if provider == "hqapi":
            return await client.get(
                self.settings.hqapi_screenshot_base_url,
                params={
                    "url": url,
                    "width": width,
                    "height": height,
                    "full_page": int(request.full_page),
                    "format": fmt,
                    "key": self.settings.hqapi_screenshot_key,
                },
            )
        raise GatewayError(f"未知截图 provider: {provider}", status_code=400)

    async def _capture_from_response(
        self,
        client: httpx.AsyncClient,
        provider: str,
        response: httpx.Response,
    ) -> ScreenshotCapture:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type.startswith("image/"):
            return ScreenshotCapture(provider=provider, content=response.content, content_type=content_type)

        data = response.json()
        image_url = self._extract_image_url(data)
        if not image_url:
            raise GatewayError(f"{provider} 未返回截图 URL 或图片内容", status_code=502)
        image_response = await client.get(image_url)
        image_response.raise_for_status()
        image_content_type = image_response.headers.get("content-type", "").split(";", 1)[0].lower() or "image/png"
        if not image_content_type.startswith("image/"):
            raise GatewayError(f"{provider} 截图 URL 未返回图片", status_code=502)
        return ScreenshotCapture(provider=provider, content=image_response.content, content_type=image_content_type)

    @staticmethod
    def _extract_image_url(data: Any) -> str:
        if not isinstance(data, dict):
            return ""
        candidates = [
            data.get("url"),
            data.get("screenshot"),
            data.get("image"),
            data.get("image_url"),
            data.get("screenshot_url"),
            data.get("renderUrl"),
            data.get("output"),
        ]
        nested = data.get("data")
        if isinstance(nested, dict):
            candidates.extend(
                [
                    nested.get("url"),
                    nested.get("screenshot"),
                    nested.get("image"),
                    nested.get("image_url"),
                    nested.get("screenshot_url"),
                ]
            )
            screenshot = nested.get("screenshot")
            if isinstance(screenshot, dict):
                candidates.extend([screenshot.get("url"), screenshot.get("href")])
        for value in candidates:
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value
        return ""

    def _provider_order(self, provider: str) -> list[str]:
        if provider != "auto":
            return [provider]
        names = [item.strip() for item in self.settings.screenshot_provider_order.split(",") if item.strip()]
        return [name for name in names if name in SCREENSHOT_PROVIDERS and name != "auto"]

    def _is_configured(self, provider: str) -> bool:
        return {
            "snapapi": bool(self.settings.snapapi_api_key and self.settings.snapapi_base_url),
            "apiflash": bool(self.settings.apiflash_access_key and self.settings.apiflash_base_url),
            "microlink": bool(self.settings.microlink_base_url),
            "screenshotlayer": bool(self.settings.screenshotlayer_access_key and self.settings.screenshotlayer_base_url),
            "phantomjscloud": bool(self.settings.phantomjscloud_api_key and self.settings.phantomjscloud_base_url),
            "screenshotbase": bool(self.settings.screenshotbase_api_key and self.settings.screenshotbase_base_url),
            "screenshotscout": bool(self.settings.screenshotscout_access_key and self.settings.screenshotscout_base_url),
            "screenshotmachine": bool(self.settings.screenshotmachine_key and self.settings.screenshotmachine_base_url),
            "thumbnailws": bool(self.settings.thumbnail_ws_api_key and self.settings.thumbnail_ws_base_url),
            "hqapi": bool(self.settings.hqapi_screenshot_key and self.settings.hqapi_screenshot_base_url),
        }.get(provider, False)

    def configured_providers(self) -> list[str]:
        return [provider for provider in self._provider_order("auto") if self._is_configured(provider)]

    def _validate_target_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise GatewayError("截图 URL 只支持 http/https", status_code=422)
        if self.settings.screenshot_allow_private_urls:
            return
        hostname = parsed.hostname.lower()
        if hostname in {"localhost", "localhost.localdomain"}:
            raise GatewayError("默认禁止截图 localhost/private URL", status_code=422)
        try:
            addresses = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise GatewayError("截图 URL 域名无法解析", status_code=422) from exc
        for item in addresses:
            address = item[4][0]
            try:
                ip = ipaddress.ip_address(address)
            except ValueError:
                continue
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
                raise GatewayError("默认禁止截图 localhost/private URL", status_code=422)

    @staticmethod
    def _cache_key(request: ScreenshotRequest) -> str:
        normalized = request.model_dump(mode="json")
        digest = hashlib.sha256(repr(sorted(normalized.items())).encode("utf-8")).hexdigest()
        return f"screenshot:{digest}"

    @staticmethod
    def _delay_seconds(delay_ms: int) -> int:
        return max(0, min(30, round(delay_ms / 1000)))

    @staticmethod
    def _playwright_wait_until(wait_until: str) -> str:
        return {
            "page_loaded": "load",
            "network_idle": "networkidle2",
            "dom_loaded": "domcontentloaded",
        }.get(wait_until, "load")

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        if isinstance(exc, GatewayError):
            return exc.message
        return type(exc).__name__
