from typing import Any

from app.config import Settings
from app.schemas.common import SearchResult
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call


class OpenAlexProvider:
    name = "openalex"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        if not self.settings.openalex_base_url:
            raise GatewayError("OpenAlex Base URL 未配置", status_code=500)

        async def request() -> list[SearchResult]:
            params = {"search": query, "per-page": max_results}
            if self.settings.open_data_contact_email:
                params["mailto"] = self.settings.open_data_contact_email
            async with build_client(self.settings, timeout=self.settings.openalex_timeout_seconds) as client:
                resp = await client.get(
                    f"{self.settings.openalex_base_url}/works",
                    params=params,
                    headers={"Accept": "application/json", "User-Agent": self.settings.open_data_user_agent},
                )
                resp.raise_for_status()
                data = resp.json()
            return self._results_from_response(data, max_results)

        return await timed_call("OpenAlex", request)

    @classmethod
    def _results_from_response(cls, data: Any, max_results: int) -> list[SearchResult]:
        items = data.get("results") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []

        results: list[SearchResult] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = cls._first_text(item, "display_name", "title")
            url = cls._best_url(item)
            if not title or not url:
                continue
            year = item.get("publication_year")
            abstract = cls._abstract_from_inverted_index(item.get("abstract_inverted_index"))
            cited_by = item.get("cited_by_count")
            meta = []
            if isinstance(year, int):
                meta.append(str(year))
            if isinstance(cited_by, int):
                meta.append(f"{cited_by} citations")
            if abstract:
                meta.append(abstract)
            results.append(SearchResult(title=title, url=url, snippet=" | ".join(meta)[:800]))
            if len(results) >= max_results:
                break
        return results

    @classmethod
    def _best_url(cls, item: dict[str, Any]) -> str:
        primary_location = item.get("primary_location")
        if isinstance(primary_location, dict):
            source_url = cls._first_text(primary_location, "landing_page_url", "pdf_url")
            if source_url:
                return source_url
        ids = item.get("ids")
        if isinstance(ids, dict):
            doi = cls._first_text(ids, "doi")
            if doi:
                return doi
        return cls._first_text(item, "id")

    @staticmethod
    def _abstract_from_inverted_index(value: Any) -> str:
        if not isinstance(value, dict):
            return ""
        positions: dict[int, str] = {}
        for word, indexes in value.items():
            if not isinstance(word, str) or not isinstance(indexes, list):
                continue
            for index in indexes:
                if isinstance(index, int):
                    positions[index] = word
        if not positions:
            return ""
        return " ".join(positions[index] for index in sorted(positions))

    @staticmethod
    def _first_text(item: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
