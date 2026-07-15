from typing import Any

from app.config import Settings
from app.schemas.common import SearchResult
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call


class WikidataProvider:
    name = "wikidata"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        if not self.settings.wikidata_base_url:
            raise GatewayError("Wikidata Base URL 未配置", status_code=500)

        async def request() -> list[SearchResult]:
            async with build_client(self.settings, timeout=self.settings.wikidata_timeout_seconds) as client:
                resp = await client.get(
                    self.settings.wikidata_base_url,
                    params={
                        "action": "wbsearchentities",
                        "search": query,
                        "language": "en",
                        "limit": max_results,
                        "format": "json",
                    },
                    headers={"Accept": "application/json", "User-Agent": self.settings.open_data_user_agent},
                )
                resp.raise_for_status()
                data = resp.json()
            return self._results_from_response(data, max_results)

        return await timed_call("Wikidata", request)

    @classmethod
    def _results_from_response(cls, data: Any, max_results: int) -> list[SearchResult]:
        items = data.get("search") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []

        results: list[SearchResult] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            entity_id = cls._first_text(item, "id")
            if not entity_id:
                continue
            title = cls._first_text(item, "label") or entity_id
            url = cls._first_text(item, "concepturi") or f"https://www.wikidata.org/wiki/{entity_id}"
            description = cls._first_text(item, "description")
            aliases = item.get("aliases")
            alias_text = ", ".join(str(alias) for alias in aliases[:5] if str(alias).strip()) if isinstance(aliases, list) else ""
            snippet = description if not alias_text else f"{description} | aliases: {alias_text}".strip(" |")
            results.append(SearchResult(title=title, url=url, snippet=snippet[:800]))
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
