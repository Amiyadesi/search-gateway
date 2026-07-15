import re
from html import unescape
from typing import Any

from app.config import Settings
from app.schemas.common import SearchResult
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call


class CrossrefProvider:
    name = "crossref"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        if not self.settings.crossref_base_url:
            raise GatewayError("Crossref Base URL 未配置", status_code=500)

        async def request() -> list[SearchResult]:
            async with build_client(self.settings, timeout=self.settings.crossref_timeout_seconds) as client:
                resp = await client.get(
                    f"{self.settings.crossref_base_url}/works",
                    params={"query": query, "rows": max_results},
                    headers={"Accept": "application/json"},
                )
                resp.raise_for_status()
                data = resp.json()
            return self._results_from_response(data, max_results)

        return await timed_call("Crossref", request)

    @classmethod
    def _results_from_response(cls, data: Any, max_results: int) -> list[SearchResult]:
        items = data.get("message", {}).get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []

        results: list[SearchResult] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = cls._first_list_text(item.get("title")) or cls._first_text(item, "DOI")
            url = cls._first_text(item, "URL")
            if not url:
                doi = cls._first_text(item, "DOI")
                url = f"https://doi.org/{doi}" if doi else ""
            if not title or not url:
                continue
            journal = cls._first_list_text(item.get("container-title"))
            published = cls._date_parts(item.get("published-print") or item.get("published-online"))
            abstract = cls._clean_html(cls._first_text(item, "abstract"))
            snippet = " | ".join(part for part in [journal, published, abstract] if part)[:800]
            results.append(SearchResult(title=title, url=url, snippet=snippet))
            if len(results) >= max_results:
                break
        return results

    @staticmethod
    def _first_list_text(value: Any) -> str:
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    return item.strip()
        return ""

    @staticmethod
    def _date_parts(value: Any) -> str:
        parts = value.get("date-parts") if isinstance(value, dict) else None
        first = parts[0] if isinstance(parts, list) and parts else None
        if not isinstance(first, list):
            return ""
        return "-".join(str(part) for part in first if isinstance(part, int))

    @staticmethod
    def _clean_html(text: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", unescape(text))).strip()

    @staticmethod
    def _first_text(item: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
