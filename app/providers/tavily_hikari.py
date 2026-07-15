import json
from typing import Any

from app.config import Settings
from app.schemas.common import SearchResult
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call


class TavilyHikariProvider:
    name = "tavily_hikari"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        if not self.settings.tavily_hikari_token:
            raise GatewayError("Tavily Hikari Token 未配置", status_code=500)
        if not self.settings.tavily_hikari_url:
            raise GatewayError("Tavily Hikari endpoint 未配置", status_code=500)

        async def request() -> list[SearchResult]:
            async with build_client(self.settings) as client:
                resp = await client.post(
                    self.settings.tavily_hikari_url,
                    headers={
                        "Authorization": f"Bearer {self.settings.tavily_hikari_token}",
                        "Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "tavily_search",
                            "arguments": {
                                "query": query,
                                "search_depth": "fast",
                                "max_results": max_results,
                            },
                        },
                    },
                )
                resp.raise_for_status()
                data = self._parse_mcp_response(resp.text)

            if "error" in data:
                raise GatewayError("Tavily Hikari MCP 返回错误", status_code=502, detail=data["error"])

            return self._results_from_mcp_result(data.get("result") or {}, max_results)

        return await timed_call("Tavily Hikari", request)

    @staticmethod
    def _parse_mcp_response(text: str) -> dict[str, Any]:
        stripped = text.strip()
        if not stripped:
            raise GatewayError("Tavily Hikari MCP 返回空响应", status_code=502)

        if stripped.startswith("{"):
            return json.loads(stripped)

        for line in stripped.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                payload = line.removeprefix("data:").strip()
                if payload and payload != "[DONE]":
                    return json.loads(payload)

        raise GatewayError("Tavily Hikari MCP 返回非 JSON/SSE 响应", status_code=502)

    @staticmethod
    def _results_from_mcp_result(result: dict[str, Any], max_results: int) -> list[SearchResult]:
        structured = result.get("structuredContent")
        if isinstance(structured, dict) and isinstance(structured.get("results"), list):
            raw_results = structured["results"]
        else:
            raw_results = TavilyHikariProvider._results_from_content_text(result)

        return [
            SearchResult(
                title=item.get("title") or "",
                url=item.get("url") or "",
                snippet=item.get("content") or item.get("snippet") or "",
            )
            for item in raw_results[:max_results]
            if isinstance(item, dict)
        ]

    @staticmethod
    def _results_from_content_text(result: dict[str, Any]) -> list[dict[str, Any]]:
        content = result.get("content")
        if not isinstance(content, list):
            return []

        for item in content:
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                continue
            try:
                parsed = json.loads(item["text"])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and isinstance(parsed.get("results"), list):
                return parsed["results"]
        return []
