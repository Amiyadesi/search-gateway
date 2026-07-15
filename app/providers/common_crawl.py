import json
from typing import Any

from app.config import Settings
from app.schemas.common import SearchResult
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call


class CommonCrawlProvider:
    name = "common_crawl"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        if not self.settings.common_crawl_index_url:
            raise GatewayError("Common Crawl Index URL 未配置", status_code=500)

        async def request() -> list[SearchResult]:
            async with build_client(self.settings, timeout=self.settings.common_crawl_timeout_seconds) as client:
                index_url = await self._resolve_index_url(client)
                resp = await client.get(
                    index_url,
                    params={
                        "url": self._lookup_url(query),
                        "output": "json",
                        "limit": max_results,
                    },
                    headers={"Accept": "application/json"},
                )
                resp.raise_for_status()
            return self._results_from_json_lines(resp.text, max_results)

        return await timed_call("Common Crawl", request)

    async def _resolve_index_url(self, client: Any) -> str:
        configured = self.settings.common_crawl_index_url
        if not configured.endswith("/collinfo.json"):
            return configured
        resp = await client.get(configured, headers={"Accept": "application/json"})
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            raise GatewayError("Common Crawl collinfo 返回格式无效", status_code=502)
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("cdx-api"), str) and item["cdx-api"].strip():
                return item["cdx-api"].strip()
        raise GatewayError("Common Crawl 未找到可用 index", status_code=502)

    @staticmethod
    def _lookup_url(query: str) -> str:
        value = query.strip()
        if not value:
            return value
        if value.startswith(("http://", "https://")):
            return value
        if "/" in value or "*" in value:
            return value
        if "." in value:
            return f"{value.rstrip('/')}/*"
        return value

    @classmethod
    def _results_from_json_lines(cls, text: str, max_results: int) -> list[SearchResult]:
        results: list[SearchResult] = []
        for line in text.splitlines():
            if len(results) >= max_results:
                break
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            url = cls._first_text(item, "url")
            if not url:
                continue
            timestamp = cls._first_text(item, "timestamp")
            status = cls._first_text(item, "status")
            mime = cls._first_text(item, "mime")
            digest = cls._first_text(item, "digest")
            meta = []
            if timestamp:
                meta.append(f"timestamp {timestamp}")
            if status:
                meta.append(f"status {status}")
            if mime:
                meta.append(mime)
            if digest:
                meta.append(f"digest {digest}")
            results.append(SearchResult(title=url, url=url, snippet=" | ".join(meta)[:800]))
        return results

    @staticmethod
    def _first_text(item: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, int):
                return str(value)
        return ""
