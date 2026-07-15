from app.config import Settings
from app.schemas.common import SearchResult
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call
from app.utils.logging import logger


class TavilyProvider:
    name = "tavily"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        api_keys = self.configured_api_keys(self.settings)
        if not api_keys:
            raise GatewayError("Tavily API Key 未配置", status_code=500)

        last_error: GatewayError | None = None
        for index, api_key in enumerate(api_keys, start=1):
            try:
                return await timed_call(f"Tavily[{index}]", lambda api_key=api_key: self._request(query, max_results, api_key))
            except GatewayError as exc:
                last_error = exc
                logger.warning("Tavily upstream {} 调用失败: {}", index, exc.message)

        if last_error:
            raise GatewayError(
                "Tavily 所有 upstream 调用失败",
                status_code=last_error.status_code,
                detail=last_error.detail,
            ) from last_error
        return []

    async def _request(self, query: str, max_results: int, api_key: str) -> list[SearchResult]:
        async with build_client(self.settings) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "query": query,
                    "search_depth": "fast",
                    "max_results": max_results,
                    "include_answer": False,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        return [
            SearchResult(
                title=item.get("title") or "",
                url=item.get("url") or "",
                snippet=item.get("content") or item.get("snippet") or "",
            )
            for item in data.get("results", [])[:max_results]
        ]

    @classmethod
    def configured_api_keys(cls, settings: Settings) -> list[str]:
        keys: list[str] = []
        for raw in [settings.tavily_api_key, *settings.tavily_api_keys.split(",")]:
            key = raw.strip()
            if key and key not in keys:
                keys.append(key)
        return keys

    @classmethod
    def configured_upstream_count(cls, settings: Settings) -> int:
        return len(cls.configured_api_keys(settings))
