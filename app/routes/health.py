from fastapi import APIRouter, Depends, Response, status

from app.config import Settings, get_settings
from app.providers.grok import GrokProvider
from app.providers.tavily import TavilyProvider
from app.schemas.health import HealthResponse, ProviderHealth, ReadinessResponse
from app.services.readiness_service import ReadinessService
from app.utils.auth import require_api_key
from app.services.cache_service import CacheService
from app.services.screenshot_service import ScreenshotService

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, bool]:
    return {"success": True}


@router.get("/readyz", response_model=ReadinessResponse)
async def readyz(
    response: Response,
    settings: Settings = Depends(get_settings),
) -> ReadinessResponse:
    result = await ReadinessService(settings).check()
    if not result.success:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result


@router.get("/health", response_model=HealthResponse)
async def health(_: None = Depends(require_api_key), settings: Settings = Depends(get_settings)) -> HealthResponse:
    cache = CacheService(settings)
    redis_ok = await cache.ping()
    await cache.close()
    screenshot_service = ScreenshotService(settings)
    screenshot_configured = screenshot_service.configured_providers()
    await screenshot_service.close()

    providers = {
        "searxng": ProviderHealth(configured=bool(settings.searxng_enabled and settings.searxng_base_url)),
        "brave": ProviderHealth(configured=bool(settings.brave_api_key)),
        "tavily": ProviderHealth(
            configured=bool(TavilyProvider.configured_api_keys(settings)),
            upstreams=TavilyProvider.configured_upstream_count(settings),
        ),
        "tavily_hikari": ProviderHealth(configured=bool(settings.tavily_hikari_token and settings.tavily_hikari_url)),
        "exa": ProviderHealth(configured=bool(settings.exa_api_key)),
        "zhihu": ProviderHealth(configured=bool(settings.zhihu_api_key)),
        "context7": ProviderHealth(configured=bool(settings.context7_api_key and settings.context7_base_url)),
        "duckduckgo": ProviderHealth(configured=bool(settings.duckduckgo_base_url)),
        "github": ProviderHealth(configured=bool(settings.github_search_base_url)),
        "stackexchange": ProviderHealth(configured=bool(settings.stackexchange_base_url and settings.stackexchange_site)),
        "wikipedia": ProviderHealth(configured=bool(settings.wikipedia_base_url)),
        "wikidata": ProviderHealth(configured=bool(settings.wikidata_base_url)),
        "hackernews": ProviderHealth(configured=bool(settings.hackernews_base_url)),
        "arxiv": ProviderHealth(configured=bool(settings.arxiv_base_url)),
        "openalex": ProviderHealth(configured=bool(settings.openalex_base_url)),
        "crossref": ProviderHealth(configured=bool(settings.crossref_base_url)),
        "pubmed": ProviderHealth(configured=bool(settings.pubmed_base_url)),
        "semantic_scholar": ProviderHealth(configured=bool(settings.semantic_scholar_base_url)),
        "internet_archive": ProviderHealth(configured=bool(settings.internet_archive_base_url)),
        "common_crawl": ProviderHealth(configured=bool(settings.common_crawl_index_url)),
        "rerank": ProviderHealth(
            configured=bool(
                settings.rerank_enabled and settings.rerank_base_url and settings.rerank_api_key and settings.rerank_model
            ),
            model=settings.rerank_model or None,
        ),
        "embedding": ProviderHealth(
            configured=bool(settings.embedding_base_url and settings.embedding_api_key and settings.embedding_model),
            model=settings.embedding_model or None,
        ),
        "grok": ProviderHealth(
            configured=GrokProvider.configured_backend_ready(settings),
            model=settings.grok_search_model,
            upstreams=GrokProvider.configured_backend_count(settings),
        ),
        "ipinfo": ProviderHealth(configured=bool(settings.ipinfo_enabled and settings.ipinfo_api_key)),
        "firecrawl": ProviderHealth(configured=bool(settings.firecrawl_api_key)),
        "screenshot": ProviderHealth(configured=bool(screenshot_configured), upstreams=len(screenshot_configured)),
        "summary": ProviderHealth(
            configured=bool(settings.summary_api_key or settings.openai_api_key or settings.deepseek_api_key),
            model=settings.summary_model,
        ),
        "answer_api": ProviderHealth(
            configured=bool(settings.answer_api_base_url and settings.answer_api_model),
            model=settings.answer_api_model or None,
        ),
    }
    return HealthResponse(success=True, api="ok", redis="ok" if redis_ok else "unavailable", providers=providers)
