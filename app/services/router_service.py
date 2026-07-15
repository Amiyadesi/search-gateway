import hashlib
import json
import re

from app.config import Settings
from app.providers.arxiv import ArxivProvider
from app.providers.brave import BraveProvider
from app.providers.common_crawl import CommonCrawlProvider
from app.providers.context7 import Context7Provider
from app.providers.crossref import CrossrefProvider
from app.providers.duckduckgo import DuckDuckGoProvider
from app.providers.exa import ExaProvider
from app.providers.github import GitHubProvider
from app.providers.grok import GrokProvider
from app.providers.hackernews import HackerNewsProvider
from app.providers.internet_archive import InternetArchiveProvider
from app.providers.openalex import OpenAlexProvider
from app.providers.pubmed import PubMedProvider
from app.providers.searxng import SearxngProvider
from app.providers.semantic_scholar import SemanticScholarProvider
from app.providers.stackexchange import StackExchangeProvider
from app.providers.tavily import TavilyProvider
from app.providers.tavily_hikari import TavilyHikariProvider
from app.providers.wikidata import WikidataProvider
from app.providers.wikipedia import WikipediaProvider
from app.schemas.common import SearchResult
from app.schemas.search import SearchResponse
from app.services.cache_service import CacheService
from app.services.rerank_service import RerankService
from app.utils.logging import logger


TECH_PATTERN = re.compile(
    r"\b(ai|llm|paper|论文|arxiv|research|github|python|fastapi|docker|kubernetes|rust|typescript|算法|模型|技术)\b",
    re.I,
)
AGENT_PATTERN = re.compile(r"\b(agent|代理|智能体|实时|quick|fast|latest|today|news|当前|最新)\b", re.I)


class RouterService:
    """根据 query 语义选择 provider，并负责搜索缓存。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.cache = CacheService(settings)
        self.reranker = RerankService(settings)
        self.providers = {
            "searxng": SearxngProvider(settings),
            "brave": BraveProvider(settings),
            "tavily": TavilyProvider(settings),
            "tavily_hikari": TavilyHikariProvider(settings),
            "exa": ExaProvider(settings),
            "context7": Context7Provider(settings),
            "duckduckgo": DuckDuckGoProvider(settings),
            "github": GitHubProvider(settings),
            "stackexchange": StackExchangeProvider(settings),
            "wikipedia": WikipediaProvider(settings),
            "wikidata": WikidataProvider(settings),
            "hackernews": HackerNewsProvider(settings),
            "arxiv": ArxivProvider(settings),
            "openalex": OpenAlexProvider(settings),
            "crossref": CrossrefProvider(settings),
            "pubmed": PubMedProvider(settings),
            "semantic_scholar": SemanticScholarProvider(settings),
            "internet_archive": InternetArchiveProvider(settings),
            "common_crawl": CommonCrawlProvider(settings),
            "grok": GrokProvider(settings),
        }

    def select_provider(self, query: str, provider: str = "auto") -> str:
        if provider != "auto":
            return provider
        grok_auto_ready = bool(
            self.settings.grok_search_enabled
            and self.settings.grok_search_auto_enabled
            and GrokProvider.configured_backend_ready(self.settings)
        )
        if AGENT_PATTERN.search(query):
            if grok_auto_ready:
                return "grok"
            return "tavily"
        if TECH_PATTERN.search(query):
            return "exa"
        return "brave"

    async def search(self, query: str, provider: str = "auto", max_results: int | None = None) -> SearchResponse:
        chosen = self.select_provider(query, provider)
        limit = max_results or self.settings.max_search_results
        grok_auto_ready = bool(
            self.settings.grok_search_enabled
            and self.settings.grok_search_auto_enabled
            and GrokProvider.configured_backend_ready(self.settings)
        )
        provider_order = self._provider_order(
            chosen,
            allow_fallback=provider == "auto",
            grok_enabled=grok_auto_ready,
        )

        last_error: Exception | None = None
        last_empty: SearchResponse | None = None
        for current in provider_order:
            try:
                response = await self._search_with_provider(current, query, limit)
                if provider == "auto" and not response.results:
                    last_empty = response
                    logger.warning("Provider {} 返回空结果，尝试下一个兜底", current)
                    continue
                return response
            except Exception as exc:
                last_error = exc
                if provider != "auto":
                    raise
                logger.warning("Provider {} 失败，尝试下一个兜底: {}", current, exc)

        if last_empty:
            return last_empty
        if last_error:
            raise last_error
        return await self._search_with_provider(chosen, query, limit)

    async def _search_with_provider(self, provider: str, query: str, limit: int) -> SearchResponse:
        cache_key = self._cache_key(provider, query, limit, self._cache_variant(provider))

        cached = await self.cache.get_json(cache_key)
        if cached is not None:
            results = [SearchResult(**item) for item in cached]
            return SearchResponse(success=True, provider=provider, query=query, cached=True, results=results)

        results = await self.providers[provider].search(query, limit)
        results = await self.reranker.rerank(query, results)
        await self.cache.set_json(cache_key, [item.model_dump(mode="json") for item in results])
        return SearchResponse(success=True, provider=provider, query=query, cached=False, results=results)

    @staticmethod
    def _provider_order(chosen: str, allow_fallback: bool, grok_enabled: bool = False) -> list[str]:
        if not allow_fallback:
            return [chosen]
        order = [chosen]
        fallback_names = ["brave", "tavily", "tavily_hikari", "exa", "searxng"]
        if grok_enabled:
            fallback_names.insert(0, "grok")
        for name in fallback_names:
            if name not in order:
                order.append(name)
        return order

    async def close(self) -> None:
        await self.cache.close()

    @staticmethod
    def _cache_key(provider: str, query: str, limit: int, variant: str = "") -> str:
        normalized = " ".join(query.lower().split())
        digest = hashlib.sha256(f"{provider}:{limit}:{variant}:{normalized}".encode("utf-8")).hexdigest()
        return f"search:{digest}"

    def _cache_variant(self, provider: str = "") -> str:
        parts: list[str] = []
        if not self.reranker.enabled:
            parts.append("rerank:off")
        else:
            parts.append(f"rerank:on:{self.settings.rerank_model}:{self.settings.rerank_top_n}")
        if provider == "grok":
            parts.append(
                "grok:"
                f"{self.settings.grok_backend}:"
                f"{self.settings.grok_search_model}:"
                f"{self._grok_upstreams_variant()}:"
                f"{self.settings.groksearch_bridge_url}:"
                f"{self.settings.groksearch_extra_sources}"
            )
        return "|".join(parts)

    def _grok_upstreams_variant(self) -> str:
        try:
            raw = [
                {
                    "name": item.name,
                    "base_url": item.base_url,
                    "model": item.model,
                }
                for item in GrokProvider.configured_upstreams(self.settings)
            ]
        except Exception:
            raw = self.settings.grok_upstreams
        digest = hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        return digest[:16]
