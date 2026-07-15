from app.config import Settings
from app.schemas.common import SearchResult
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call


class BraveProvider:
    name = "brave"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        if not self.settings.brave_api_key:
            raise GatewayError("Brave API Key 未配置", status_code=500)

        async def request() -> list[SearchResult]:
            # Brave 在当前 VPS 上偶发直连超时；缩短超时，auto 模式能更快走兜底 provider。
            async with build_client(self.settings, timeout=8.0) as client:
                resp = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    headers={
                        "X-Subscription-Token": self.settings.brave_api_key,
                        "Accept": "application/json",
                    },
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

        return await timed_call("Brave", request)
