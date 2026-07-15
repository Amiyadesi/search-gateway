from app.config import Settings
from app.schemas.common import SearchResult
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call


class ExaProvider:
    name = "exa"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        if not self.settings.exa_api_key:
            raise GatewayError("Exa API Key 未配置", status_code=500)

        async def request() -> list[SearchResult]:
            async with build_client(self.settings) as client:
                resp = await client.post(
                    "https://api.exa.ai/search",
                    headers={
                        "x-api-key": self.settings.exa_api_key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "query": query,
                        "numResults": max_results,
                        "contents": {"text": True, "summary": True, "highlights": True},
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            return [self._parse_item(item) for item in data.get("results", [])[:max_results]]

        return await timed_call("Exa", request)

    @staticmethod
    def _parse_item(item: dict) -> SearchResult:
        highlights = item.get("highlights") or []
        snippet = item.get("summary") or (highlights[0] if highlights else "") or item.get("text") or ""
        return SearchResult(title=item.get("title") or "", url=item.get("url") or "", snippet=snippet[:800])
