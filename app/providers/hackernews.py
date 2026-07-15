from typing import Any

from app.config import Settings
from app.schemas.common import SearchResult
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call


class HackerNewsProvider:
    name = "hackernews"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        if not self.settings.hackernews_base_url:
            raise GatewayError("Hacker News Base URL 未配置", status_code=500)

        async def request() -> list[SearchResult]:
            async with build_client(self.settings, timeout=self.settings.hackernews_timeout_seconds) as client:
                resp = await client.get(
                    f"{self.settings.hackernews_base_url}/search",
                    params={"query": query, "tags": "story", "hitsPerPage": max_results},
                    headers={"Accept": "application/json"},
                )
                resp.raise_for_status()
                data = resp.json()
            return self._results_from_response(data, max_results)

        return await timed_call("Hacker News", request)

    @classmethod
    def _results_from_response(cls, data: Any, max_results: int) -> list[SearchResult]:
        hits = data.get("hits") if isinstance(data, dict) else None
        if not isinstance(hits, list):
            return []

        results: list[SearchResult] = []
        for item in hits:
            if not isinstance(item, dict):
                continue
            object_id = cls._first_text(item, "objectID")
            url = cls._first_text(item, "url", "story_url")
            if not url and object_id:
                url = f"https://news.ycombinator.com/item?id={object_id}"
            if not url:
                continue
            title = cls._first_text(item, "title", "story_title") or url
            points = item.get("points")
            comments = item.get("num_comments")
            author = cls._first_text(item, "author")
            meta = []
            if isinstance(points, int):
                meta.append(f"{points} points")
            if isinstance(comments, int):
                meta.append(f"{comments} comments")
            if author:
                meta.append(f"by {author}")
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
