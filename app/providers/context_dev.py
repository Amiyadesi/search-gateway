from app.config import Settings
from app.schemas.common import SearchResult
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call


class ContextDevProvider:
    name = "context_dev"
    search_url = "https://api.context.dev/v1/web/search"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        if not self.settings.context_dev_api_key:
            raise GatewayError("Context.dev API Key 未配置", status_code=500)

        async def request() -> list[SearchResult]:
            async with build_client(self.settings) as client:
                response = await client.post(
                    self.search_url,
                    headers={
                        "Authorization": f"Bearer {self.settings.context_dev_api_key}",
                        "Accept": "application/json",
                    },
                    json={"query": query, "numResults": 10},
                )
                response.raise_for_status()

            try:
                data = response.json()
            except ValueError as exc:
                raise GatewayError("Context.dev 返回了无效 JSON", status_code=502) from exc
            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                raise GatewayError("Context.dev 返回了无效搜索结果", status_code=502)

            results = []
            for item in data["results"][:max_results]:
                if not isinstance(item, dict) or not all(
                    isinstance(item.get(field), str) for field in ("title", "url", "description")
                ):
                    raise GatewayError("Context.dev 返回了无效搜索结果", status_code=502)
                results.append(
                    SearchResult(
                        title=item["title"],
                        url=item["url"],
                        snippet=item["description"],
                    )
                )
            return results

        return await timed_call("Context.dev", request)
