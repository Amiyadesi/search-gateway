from app.config import Settings
from app.schemas.common import SearchResult
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call
from app.utils.logging import logger


class BraveProvider:
    name = "brave"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        api_keys = self.configured_api_keys(self.settings)
        if not api_keys:
            raise GatewayError("Brave API Key 未配置", status_code=500)

        last_error: GatewayError | None = None
        for index, api_key in enumerate(api_keys, start=1):
            try:
                return await timed_call(
                    f"Brave[{index}]",
                    lambda api_key=api_key: self._request(query, max_results, api_key),
                )
            except GatewayError as exc:
                last_error = exc
                logger.warning("Brave upstream {} 调用失败: {}", index, exc.message)

        if last_error:
            raise GatewayError(
                "Brave 所有 upstream 调用失败",
                status_code=last_error.status_code,
                detail=last_error.detail,
            ) from last_error
        return []

    async def _request(self, query: str, max_results: int, api_key: str) -> list[SearchResult]:
        # Brave 在当前 VPS 上偶发直连超时；缩短超时，auto 模式能更快走兜底 provider。
        async with build_client(self.settings, timeout=8.0) as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
                params={"q": query, "count": max_results, "search_lang": "en"},
            )
            resp.raise_for_status()
            data = resp.json()
        items = data.get("web", {}).get("results", [])
        return [
            SearchResult(
                title=item.get("title") or "",
                url=item.get("url") or "",
                snippet=item.get("description") or "",
            )
            for item in items[:max_results]
        ]

    @staticmethod
    def configured_api_keys(settings: Settings) -> list[str]:
        keys: list[str] = []
        for raw in [settings.brave_api_key, *settings.brave_api_keys.split(",")]:
            key = raw.strip()
            if key and key not in keys:
                keys.append(key)
        return keys

    @classmethod
    def configured_upstream_count(cls, settings: Settings) -> int:
        return len(cls.configured_api_keys(settings))
