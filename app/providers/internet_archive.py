from typing import Any
from urllib.parse import quote

from app.config import Settings
from app.schemas.common import SearchResult
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call


class InternetArchiveProvider:
    name = "internet_archive"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        if not self.settings.internet_archive_base_url:
            raise GatewayError("Internet Archive Base URL 未配置", status_code=500)

        async def request() -> list[SearchResult]:
            params = [
                ("q", query),
                ("fl[]", "identifier"),
                ("fl[]", "title"),
                ("fl[]", "description"),
                ("rows", str(max_results)),
                ("output", "json"),
            ]
            async with build_client(self.settings, timeout=self.settings.internet_archive_timeout_seconds) as client:
                resp = await client.get(
                    self.settings.internet_archive_base_url,
                    params=params,
                    headers={"Accept": "application/json"},
                )
                resp.raise_for_status()
                data = resp.json()
            return self._results_from_response(data, max_results)

        return await timed_call("Internet Archive", request)

    @classmethod
    def _results_from_response(cls, data: Any, max_results: int) -> list[SearchResult]:
        docs = data.get("response", {}).get("docs") if isinstance(data, dict) else None
        if not isinstance(docs, list):
            return []

        results: list[SearchResult] = []
        for item in docs:
            if not isinstance(item, dict):
                continue
            identifier = cls._first_text(item, "identifier")
            if not identifier:
                continue
            title = cls._coerce_text(item.get("title")) or identifier
            description = cls._coerce_text(item.get("description"))
            results.append(
                SearchResult(
                    title=title,
                    url=f"https://archive.org/details/{quote(identifier, safe='')}",
                    snippet=description[:800],
                )
            )
            if len(results) >= max_results:
                break
        return results

    @staticmethod
    def _coerce_text(value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            return " ".join(str(item).strip() for item in value if str(item).strip())
        return ""

    @staticmethod
    def _first_text(item: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
