from html import unescape
from typing import Any

from app.config import Settings
from app.schemas.common import SearchResult
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call


class StackExchangeProvider:
    name = "stackexchange"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        if not self.settings.stackexchange_base_url:
            raise GatewayError("Stack Exchange Base URL 未配置", status_code=500)
        if not self.settings.stackexchange_site:
            raise GatewayError("Stack Exchange site 未配置", status_code=500)

        async def request() -> list[SearchResult]:
            params = {
                "order": "desc",
                "sort": "relevance",
                "site": self.settings.stackexchange_site,
                "q": query,
                "pagesize": max_results,
                "filter": "default",
            }
            if self.settings.stackexchange_key:
                params["key"] = self.settings.stackexchange_key

            async with build_client(self.settings, timeout=self.settings.stackexchange_timeout_seconds) as client:
                resp = await client.get(
                    f"{self.settings.stackexchange_base_url}/search/advanced",
                    headers={"Accept": "application/json"},
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()
            return self._results_from_response(data, max_results)

        return await timed_call("Stack Exchange", request)

    @classmethod
    def _results_from_response(cls, data: Any, max_results: int) -> list[SearchResult]:
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []

        results: list[SearchResult] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            url = cls._first_text(item, "link")
            if not url:
                continue
            title = unescape(cls._first_text(item, "title") or url)
            score = item.get("score")
            answer_count = item.get("answer_count")
            tags = item.get("tags")
            meta = []
            if isinstance(score, int):
                meta.append(f"score {score}")
            if isinstance(answer_count, int):
                meta.append(f"{answer_count} answers")
            if isinstance(tags, list):
                safe_tags = [str(tag) for tag in tags[:5] if str(tag).strip()]
                if safe_tags:
                    meta.append(", ".join(safe_tags))
            results.append(SearchResult(title=title, url=url, snippet=" | ".join(meta)[:800]))
            if len(results) >= max_results:
                break
        return results

    @staticmethod
    def _first_text(item: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
