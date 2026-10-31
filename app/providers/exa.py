from app.config import Settings
from app.schemas.common import SearchResult
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call
from app.utils.logging import logger


class ExaProvider:
    name = "exa"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        api_keys = self.configured_api_keys(self.settings)
        if not api_keys:
            raise GatewayError("Exa API Key 未配置", status_code=500)

        last_error: GatewayError | None = None
        for index, api_key in enumerate(api_keys, start=1):
            try:
                return await timed_call(
                    f"Exa[{index}]",
                    lambda api_key=api_key: self._request(query, max_results, api_key),
                )
            except GatewayError as exc:
                last_error = exc
                logger.warning("Exa upstream {} 调用失败: {}", index, exc.message)

        if last_error:
            raise GatewayError(
                "Exa 所有 upstream 调用失败",
                status_code=last_error.status_code,
                detail=last_error.detail,
            ) from last_error
        return []

    async def _request(self, query: str, max_results: int, api_key: str) -> list[SearchResult]:
        async with build_client(self.settings) as client:
            resp = await client.post(
                f"{self.settings.exa_api_url}/search",
                headers={"x-api-key": api_key, "Content-Type": "application/json"},
                json={
                    "query": query,
                    "numResults": max_results,
                    "contents": {"text": True, "summary": True, "highlights": True},
                },
            )
            resp.raise_for_status()
            data = resp.json()
        return [self._parse_item(item) for item in data.get("results", [])[:max_results]]

    @staticmethod
    def configured_api_keys(settings: Settings) -> list[str]:
        keys: list[str] = []
        for raw in [settings.exa_api_key, *settings.exa_api_keys.split(",")]:
            key = raw.strip()
            if key and key not in keys:
                keys.append(key)
        return keys

    @classmethod
    def configured_upstream_count(cls, settings: Settings) -> int:
        return len(cls.configured_api_keys(settings))

    @staticmethod
    def _parse_item(item: dict) -> SearchResult:
        highlights = item.get("highlights") or []
        snippet = item.get("summary") or (highlights[0] if highlights else "") or item.get("text") or ""
        return SearchResult(title=item.get("title") or "", url=item.get("url") or "", snippet=snippet[:800])
