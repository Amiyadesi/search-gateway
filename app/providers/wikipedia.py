import re
from html import unescape
from typing import Any
from urllib.parse import quote

from app.config import Settings
from app.schemas.common import SearchResult
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call


class WikipediaProvider:
    name = "wikipedia"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        if not self.settings.wikipedia_base_url:
            raise GatewayError("Wikipedia Base URL 未配置", status_code=500)

        async def request() -> list[SearchResult]:
            async with build_client(self.settings, timeout=self.settings.wikipedia_timeout_seconds) as client:
                resp = await client.get(
                    self.settings.wikipedia_base_url,
                    params={
                        "action": "query",
                        "list": "search",
                        "srsearch": query,
                        "srlimit": max_results,
                        "format": "json",
                        "utf8": "1",
                    },
                    headers={"Accept": "application/json", "User-Agent": self.settings.open_data_user_agent},
                )
                resp.raise_for_status()
                data = resp.json()
            return self._results_from_response(data, max_results)

        return await timed_call("Wikipedia", request)

    @classmethod
    def _results_from_response(cls, data: Any, max_results: int) -> list[SearchResult]:
        items = data.get("query", {}).get("search") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []

        results: list[SearchResult] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = cls._first_text(item, "title")
            if not title:
                continue
            results.append(
                SearchResult(
                    title=title,
                    url=f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'), safe='()_')}",
                    snippet=cls._clean_snippet(cls._first_text(item, "snippet"))[:800],
                )
            )
            if len(results) >= max_results:
                break
        return results

    @staticmethod
    def _clean_snippet(text: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", unescape(text))).strip()

    @staticmethod
    def _first_text(item: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
