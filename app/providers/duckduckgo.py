from typing import Any

from app.config import Settings
from app.schemas.common import SearchResult
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call


class DuckDuckGoProvider:
    name = "duckduckgo"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        if not self.settings.duckduckgo_base_url:
            raise GatewayError("DuckDuckGo Base URL 未配置", status_code=500)

        async def request() -> list[SearchResult]:
            async with build_client(self.settings, timeout=self.settings.duckduckgo_timeout_seconds) as client:
                resp = await client.get(
                    self.settings.duckduckgo_base_url,
                    params={
                        "q": query,
                        "format": "json",
                        "no_html": "1",
                        "skip_disambig": "1",
                    },
                    headers={"Accept": "application/json"},
                )
                resp.raise_for_status()
                data = resp.json()
            return self._results_from_response(data, max_results)

        return await timed_call("DuckDuckGo", request)

    @classmethod
    def _results_from_response(cls, data: Any, max_results: int) -> list[SearchResult]:
        if not isinstance(data, dict):
            return []

        results: list[SearchResult] = []
        abstract_url = cls._first_text(data, "AbstractURL")
        abstract_text = cls._first_text(data, "AbstractText")
        if abstract_url and abstract_text:
            results.append(
                SearchResult(
                    title=cls._first_text(data, "Heading") or abstract_url,
                    url=abstract_url,
                    snippet=abstract_text[:800],
                )
            )

        cls._extend_topic_results(results, data.get("Results"), max_results)
        cls._extend_topic_results(results, data.get("RelatedTopics"), max_results)
        return results[:max_results]

    @classmethod
    def _extend_topic_results(cls, results: list[SearchResult], topics: Any, max_results: int) -> None:
        if not isinstance(topics, list):
            return

        for item in topics:
            if len(results) >= max_results:
                return
            if not isinstance(item, dict):
                continue
            nested = item.get("Topics")
            if isinstance(nested, list):
                cls._extend_topic_results(results, nested, max_results)
                continue
            url = cls._first_text(item, "FirstURL")
            if not url:
                continue
            text = cls._first_text(item, "Text")
            title = text.split(" - ", 1)[0].strip() if text else url
            results.append(SearchResult(title=title, url=url, snippet=text[:800]))

    @staticmethod
    def _first_text(item: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
