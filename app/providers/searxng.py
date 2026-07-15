from app.config import Settings
from app.schemas.common import SearchResult
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call


class SearxngProvider:
    name = "searxng"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        if not self.settings.searxng_enabled:
            raise GatewayError("SearXNG 未启用", status_code=500)
        if not self.settings.searxng_base_url:
            raise GatewayError("SearXNG Base URL 未配置", status_code=500)

        async def request() -> list[SearchResult]:
            async with build_client(self.settings, timeout=self.settings.searxng_timeout_seconds) as client:
                resp = await client.get(
                    f"{self.settings.searxng_base_url}/search",
                    params={
                        "q": query,
                        "format": "json",
                    },
                    headers={"Accept": "application/json"},
                )
                resp.raise_for_status()
                data = resp.json()

            results = []
            for item in data.get("results", [])[:max_results]:
                results.append(
                    SearchResult(
                        title=item.get("title") or "",
                        url=item.get("url") or "",
                        snippet=item.get("content") or "",
                    )
                )
            return results

        return await timed_call("SearXNG", request)
