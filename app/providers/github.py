from typing import Any

from app.config import Settings
from app.schemas.common import SearchResult
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call


class GitHubProvider:
    name = "github"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        if not self.settings.github_search_base_url:
            raise GatewayError("GitHub Search Base URL 未配置", status_code=500)

        async def request() -> list[SearchResult]:
            headers = {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            if self.settings.github_token:
                headers["Authorization"] = f"Bearer {self.settings.github_token}"

            async with build_client(self.settings, timeout=self.settings.github_timeout_seconds) as client:
                resp = await client.get(
                    f"{self.settings.github_search_base_url}/repositories",
                    headers=headers,
                    params={
                        "q": query,
                        "per_page": max_results,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            return self._results_from_response(data, max_results)

        return await timed_call("GitHub", request)

    @classmethod
    def _results_from_response(cls, data: Any, max_results: int) -> list[SearchResult]:
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []

        results: list[SearchResult] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            url = cls._first_text(item, "html_url")
            if not url:
                continue
            title = cls._first_text(item, "full_name", "name") or url
            description = cls._first_text(item, "description")
            language = cls._first_text(item, "language")
            stars = item.get("stargazers_count")
            parts = []
            if description:
                parts.append(description)
            meta = []
            if language:
                meta.append(language)
            if isinstance(stars, int):
                meta.append(f"{stars} stars")
            if meta:
                parts.append(" | ".join(meta))
            results.append(SearchResult(title=title, url=url, snippet=" - ".join(parts)[:800]))
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
