from typing import Any

from app.config import Settings
from app.schemas.common import SearchResult
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call


class SemanticScholarProvider:
    name = "semantic_scholar"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        if not self.settings.semantic_scholar_base_url:
            raise GatewayError("Semantic Scholar Base URL 未配置", status_code=500)

        async def request() -> list[SearchResult]:
            headers = {"Accept": "application/json", "User-Agent": self.settings.open_data_user_agent}
            if self.settings.semantic_scholar_api_key:
                headers["x-api-key"] = self.settings.semantic_scholar_api_key
            async with build_client(self.settings, timeout=self.settings.semantic_scholar_timeout_seconds) as client:
                resp = await client.get(
                    f"{self.settings.semantic_scholar_base_url}/paper/search",
                    params={
                        "query": query,
                        "limit": max_results,
                        "fields": "title,url,abstract,authors,year,venue",
                    },
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
            return self._results_from_response(data, max_results)

        return await timed_call("Semantic Scholar", request)

    @classmethod
    def _results_from_response(cls, data: Any, max_results: int) -> list[SearchResult]:
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []

        results: list[SearchResult] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = cls._first_text(item, "title")
            url = cls._first_text(item, "url")
            if not title or not url:
                continue
            year = item.get("year")
            venue = cls._first_text(item, "venue")
            abstract = cls._first_text(item, "abstract")
            authors = item.get("authors")
            author_names = []
            if isinstance(authors, list):
                for author in authors[:3]:
                    if isinstance(author, dict):
                        name = cls._first_text(author, "name")
                        if name:
                            author_names.append(name)
            meta = []
            if isinstance(year, int):
                meta.append(str(year))
            if venue:
                meta.append(venue)
            if author_names:
                meta.append(", ".join(author_names))
            if abstract:
                meta.append(abstract)
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
