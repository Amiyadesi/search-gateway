import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from app.config import Settings
from app.schemas.common import SearchResult
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call
from app.utils.logging import logger


@dataclass(frozen=True)
class GrokUpstream:
    name: str
    base_url: str
    api_key: str
    model: str = ""


class GrokProvider:
    name = "grok"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        if not self.settings.grok_search_enabled:
            raise GatewayError("Grok 搜索未启用", status_code=500)
        backend = self.settings.grok_backend
        if backend == "groksearch":
            return await timed_call(
                "GrokSearchBridge",
                lambda: self._request_groksearch_bridge(query, max_results),
            )
        if backend == "hybrid":
            try:
                results = await timed_call(
                    "GrokSearchBridge",
                    lambda: self._request_groksearch_bridge(query, max_results),
                )
                if results:
                    logger.info("GrokSearch bridge 调用成功")
                    return results
                logger.warning("GrokSearch bridge 返回空结果，回退 OpenAI-compatible upstream")
            except GatewayError as exc:
                logger.warning("GrokSearch bridge 调用失败，回退 OpenAI-compatible upstream: {}", exc.message)
            return await self._search_openai_upstreams(query, max_results)
        return await self._search_openai_upstreams(query, max_results)

    async def _search_openai_upstreams(self, query: str, max_results: int) -> list[SearchResult]:
        upstreams = self.configured_upstreams(self.settings)
        if not upstreams:
            raise GatewayError("Grok upstream 未配置", status_code=500)

        last_error: GatewayError | None = None
        for upstream in upstreams:
            try:
                results = await timed_call(
                    f"Grok[{upstream.name}]",
                    lambda upstream=upstream: self._request_upstream(upstream, query, max_results),
                )
                logger.info("Grok upstream {} 调用成功", upstream.name)
                return results
            except GatewayError as exc:
                last_error = exc
                logger.warning("Grok upstream {} 调用失败: {}", upstream.name, exc.message)

        if last_error:
            raise GatewayError(
                "Grok 所有 upstream 调用失败",
                status_code=last_error.status_code,
                detail=last_error.detail,
            ) from last_error
        return []

    async def _request_groksearch_bridge(self, query: str, max_results: int) -> list[SearchResult]:
        if not self.groksearch_bridge_configured(self.settings):
            raise GatewayError("GrokSearch bridge 未配置", status_code=500)
        async with build_client(self.settings, timeout=self.settings.groksearch_bridge_timeout_seconds) as client:
            resp = await client.post(
                f"{self.settings.groksearch_bridge_url}/search",
                json={
                    "query": query,
                    "max_results": max_results,
                    "model": self.settings.grok_search_model,
                    "extra_sources": self.settings.groksearch_extra_sources,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        return self._results_from_search_sources(data.get("results"), max_results)

    async def _request_upstream(self, upstream: GrokUpstream, query: str, max_results: int) -> list[SearchResult]:
        async with build_client(self.settings, timeout=self.settings.grok_search_timeout_seconds) as client:
            resp = await client.post(
                f"{upstream.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {upstream.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": upstream.model or self.settings.grok_search_model,
                    "stream": False,
                    "max_tokens": self.settings.grok_search_max_tokens,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Use web search when useful. Return a concise JSON array of search results only. "
                                "Each item must contain title, url, and snippet."
                            ),
                        },
                        {
                            "role": "user",
                            "content": f"Search the web for: {query}\nReturn up to {max_results} results.",
                        },
                    ],
                },
            )
            resp.raise_for_status()
            data = resp.json()

        results = self._results_from_search_sources(data.get("search_sources"), max_results)
        if results:
            return results
        return self._results_from_message(data, max_results)

    @classmethod
    def configured_upstreams(cls, settings: Settings) -> list[GrokUpstream]:
        upstreams = cls._upstreams_from_json(settings.grok_upstreams)
        if upstreams:
            return upstreams
        if settings.grok_api_key and settings.grok_base_url:
            base_url = cls._normalize_base_url(settings.grok_base_url)
            return [
                GrokUpstream(
                    name=cls._upstream_name("default", base_url),
                    base_url=base_url,
                    api_key=settings.grok_api_key,
                )
            ]
        return []

    @classmethod
    def configured_upstream_count(cls, settings: Settings) -> int:
        return len(cls.configured_upstreams(settings))

    @staticmethod
    def groksearch_bridge_configured(settings: Settings) -> bool:
        return bool(settings.groksearch_bridge_url.strip())

    @classmethod
    def configured_backend_ready(cls, settings: Settings) -> bool:
        if not settings.grok_search_enabled:
            return False
        if settings.grok_backend == "groksearch":
            return cls.groksearch_bridge_configured(settings)
        if settings.grok_backend == "hybrid":
            return cls.groksearch_bridge_configured(settings) or cls.configured_upstream_count(settings) > 0
        return cls.configured_upstream_count(settings) > 0

    @classmethod
    def configured_backend_count(cls, settings: Settings) -> int:
        count = cls.configured_upstream_count(settings)
        if settings.grok_backend in {"groksearch", "hybrid"} and cls.groksearch_bridge_configured(settings):
            count += 1
        return count

    @classmethod
    def _upstreams_from_json(cls, raw: str) -> list[GrokUpstream]:
        if not raw.strip():
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GatewayError("GROK_UPSTREAMS 不是合法 JSON", status_code=500) from exc
        if not isinstance(data, list):
            raise GatewayError("GROK_UPSTREAMS 必须是 JSON array", status_code=500)

        upstreams: list[GrokUpstream] = []
        for index, item in enumerate(data, start=1):
            if not isinstance(item, dict):
                continue
            raw_base_url = item.get("base_url") or item.get("url")
            raw_key = item.get("api_key") or item.get("key")
            raw_model = item.get("model")
            if not isinstance(raw_base_url, str) or not raw_base_url.strip():
                continue
            if not isinstance(raw_key, str) or not raw_key.strip():
                continue
            model = raw_model.strip() if isinstance(raw_model, str) else ""
            base_url = cls._normalize_base_url(raw_base_url)
            name = item.get("name")
            upstreams.append(
                GrokUpstream(
                    name=cls._upstream_name(str(name), base_url) if name else cls._upstream_name(f"upstream-{index}", base_url),
                    base_url=base_url,
                    api_key=raw_key.strip(),
                    model=model,
                )
            )
        return upstreams

    @staticmethod
    def _normalize_base_url(base_url: str) -> str:
        normalized = base_url.strip().rstrip("/")
        if normalized.endswith("/v1"):
            return normalized
        return f"{normalized}/v1"

    @staticmethod
    def _upstream_name(candidate: str, base_url: str) -> str:
        cleaned = candidate.strip()
        if cleaned and cleaned.lower() not in {"default", "none", "null"}:
            return cleaned[:80]
        parsed = urlparse(base_url)
        return (parsed.netloc or "grok-upstream")[:80]

    @classmethod
    def _results_from_search_sources(cls, sources: Any, max_results: int) -> list[SearchResult]:
        if not isinstance(sources, list):
            return []

        results: list[SearchResult] = []
        for item in sources:
            if not isinstance(item, dict):
                continue
            url = cls._first_text(item, "url", "link", "uri")
            if not url:
                continue
            title = cls._first_text(item, "title", "name") or url
            snippet = cls._first_text(item, "snippet", "content", "description", "text") or ""
            results.append(SearchResult(title=title, url=url, snippet=snippet[:800]))
            if len(results) >= max_results:
                break
        return results

    @classmethod
    def _results_from_message(cls, data: dict[str, Any], max_results: int) -> list[SearchResult]:
        content = cls._message_content(data)
        if not content:
            return []
        parsed = cls._parse_jsonish(content)
        if isinstance(parsed, dict):
            parsed = parsed.get("results") or parsed.get("sources") or []
        if isinstance(parsed, list):
            results = cls._results_from_search_sources(parsed, max_results)
            if results:
                return results
        return cls._results_from_links(content, max_results)

    @classmethod
    def _results_from_links(cls, text: str, max_results: int) -> list[SearchResult]:
        results: list[SearchResult] = []
        seen: set[str] = set()
        snippet = re.sub(r"\s+", " ", text).strip()[:800]

        for label, url in re.findall(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", text):
            cleaned_url = cls._clean_url(url)
            if cleaned_url in seen:
                continue
            seen.add(cleaned_url)
            title = cls._link_title(label, cleaned_url)
            results.append(SearchResult(title=title, url=cleaned_url, snippet=snippet))
            if len(results) >= max_results:
                return results

        for url in re.findall(r"https?://[^\s\])}>,]+", text):
            cleaned_url = cls._clean_url(url)
            if cleaned_url in seen:
                continue
            seen.add(cleaned_url)
            results.append(SearchResult(title=cleaned_url, url=cleaned_url, snippet=snippet))
            if len(results) >= max_results:
                return results
        return results

    @staticmethod
    def _clean_url(url: str) -> str:
        return url.strip().rstrip(".,;:)")

    @staticmethod
    def _link_title(label: str, url: str) -> str:
        cleaned = label.strip()
        if not cleaned or cleaned.isdigit() or re.fullmatch(r"\[\d+\]", cleaned) or cleaned.startswith("http"):
            return url
        return cleaned[:160]

    @staticmethod
    def _message_content(data: dict[str, Any]) -> str:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            return ""
        content = message.get("content")
        if isinstance(content, str):
            return content
        return ""

    @staticmethod
    def _parse_jsonish(text: str) -> Any:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I).strip()
            cleaned = re.sub(r"\s*```$", "", cleaned).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"(\[[\s\S]*\]|\{[\s\S]*\})", cleaned)
            if not match:
                return None
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                return None

    @staticmethod
    def _first_text(item: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
