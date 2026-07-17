from urllib.parse import urlsplit

import httpx

from app.config import Settings
from app.schemas.health import DependencyHealth, ReadinessResponse
from app.services.cache_service import CacheService
from app.utils.http import build_client

READINESS_TIMEOUT_SECONDS = 3.0


class ReadinessService:
    """Check only internal dependencies required by the configured gateway."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def check(self) -> ReadinessResponse:
        checks = {
            "redis": await self._redis_dependency(),
            "searxng": await self._http_dependency(
                enabled=self.settings.searxng_enabled,
                base_url=self.settings.searxng_base_url,
            ),
            "groksearch_bridge": await self._http_dependency(
                enabled=(
                    self.settings.grok_search_enabled
                    and self.settings.grok_backend in {"groksearch", "hybrid"}
                ),
                base_url=self.settings.groksearch_bridge_url,
            ),
        }
        success = all(check.status == "ok" for check in checks.values() if check.required)
        return ReadinessResponse(
            success=success,
            status="ready" if success else "not_ready",
            checks=checks,
        )

    async def _redis_dependency(self) -> DependencyHealth:
        if urlsplit(self.settings.redis_url).scheme not in {"redis", "rediss", "unix"}:
            return DependencyHealth(status="misconfigured", required=True, configured=False)

        try:
            cache = CacheService(self.settings)
        except ValueError:
            return DependencyHealth(status="misconfigured", required=True, configured=False)
        try:
            redis_ok = await cache.ping()
        finally:
            await cache.close()
        return DependencyHealth(
            status="ok" if redis_ok else "unavailable",
            required=True,
            configured=True,
        )

    async def _http_dependency(self, *, enabled: bool, base_url: str) -> DependencyHealth:
        if not enabled:
            return DependencyHealth(status="disabled", required=False, configured=False)
        if not self._valid_http_base_url(base_url):
            return DependencyHealth(status="misconfigured", required=True, configured=False)

        try:
            async with build_client(self.settings, timeout=READINESS_TIMEOUT_SECONDS) as client:
                response = await client.get(f"{base_url.rstrip('/')}/healthz")
                response.raise_for_status()
        except (httpx.HTTPError, ValueError):
            return DependencyHealth(status="unavailable", required=True, configured=True)
        return DependencyHealth(status="ok", required=True, configured=True)

    @staticmethod
    def _valid_http_base_url(base_url: str) -> bool:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            return False
        try:
            parsed.port
        except ValueError:
            return False
        return True
