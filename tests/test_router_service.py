from app.config import Settings
from app.schemas.common import SearchResult
from app.schemas.search import SearchResponse
from app.services.router_service import RouterService


def test_select_provider_defaults_to_brave_when_searxng_disabled():
    service = RouterService(Settings(gateway_api_key="test"))
    assert service.select_provider("undertale") == "brave"


def test_select_provider_defaults_to_brave_when_searxng_enabled():
    service = RouterService(Settings(gateway_api_key="test", searxng_enabled=True))
    assert service.select_provider("undertale") == "brave"


def test_select_provider_tech_query_uses_exa():
    service = RouterService(Settings(gateway_api_key="test"))
    assert service.select_provider("FastAPI redis cache architecture") == "exa"


def test_select_provider_agent_query_uses_tavily():
    service = RouterService(Settings(gateway_api_key="test"))
    assert service.select_provider("latest agent framework news") == "tavily"


def test_select_provider_agent_query_uses_grok_when_auto_enabled():
    service = RouterService(Settings(gateway_api_key="test", grok_search_enabled=True, grok_api_key="gk"))
    assert service.select_provider("latest agent framework news") == "tavily"

    service = RouterService(
        Settings(
            gateway_api_key="test",
            grok_search_enabled=True,
            grok_search_auto_enabled=True,
            grok_api_key="gk",
            grok_base_url="https://grok.example/v1",
        )
    )
    assert service.select_provider("latest agent framework news") == "grok"


def test_select_provider_agent_query_uses_groksearch_bridge_when_auto_enabled():
    service = RouterService(
        Settings(
            gateway_api_key="test",
            grok_search_enabled=True,
            grok_search_auto_enabled=True,
            grok_backend="groksearch",
            groksearch_bridge_url="http://bridge:8010",
        )
    )
    assert service.select_provider("latest agent framework news") == "grok"


def test_select_provider_agent_query_ignores_grok_without_key():
    service = RouterService(
        Settings(
            gateway_api_key="test",
            grok_search_enabled=True,
            grok_backend="openai",
            grok_api_key="",
            grok_base_url="",
        )
    )
    assert service.select_provider("latest agent framework news") == "tavily"


def test_explicit_provider_wins():
    service = RouterService(Settings(gateway_api_key="test"))
    assert service.select_provider("FastAPI redis", provider="brave") == "brave"


def test_auto_fallback_order_starts_with_selected_provider():
    assert RouterService._provider_order("searxng", allow_fallback=True) == [
        "searxng",
        "brave",
        "tavily",
        "tavily_hikari",
        "exa",
    ]
    assert RouterService._provider_order("brave", allow_fallback=True) == [
        "brave",
        "tavily",
        "tavily_hikari",
        "exa",
        "searxng",
    ]
    assert RouterService._provider_order("exa", allow_fallback=True) == [
        "exa",
        "brave",
        "tavily",
        "tavily_hikari",
        "searxng",
    ]
    assert RouterService._provider_order("searxng", allow_fallback=True, grok_enabled=True) == [
        "searxng",
        "grok",
        "brave",
        "tavily",
        "tavily_hikari",
        "exa",
    ]


def test_explicit_tavily_hikari_provider_wins():
    service = RouterService(Settings(gateway_api_key="test"))
    assert service.select_provider("latest AI news", provider="tavily_hikari") == "tavily_hikari"


def test_explicit_context7_provider_wins_and_stays_out_of_auto_fallback():
    service = RouterService(Settings(gateway_api_key="test", context7_api_key="ctx"))
    assert service.select_provider("Next.js middleware docs", provider="context7") == "context7"
    assert "context7" not in RouterService._provider_order("searxng", allow_fallback=True)


def test_explicit_community_providers_win_and_stay_out_of_auto_fallback():
    service = RouterService(Settings(gateway_api_key="test"))
    assert service.select_provider("python", provider="duckduckgo") == "duckduckgo"
    assert service.select_provider("fastapi", provider="github") == "github"
    assert service.select_provider("fastapi error", provider="stackexchange") == "stackexchange"
    assert service.select_provider("Python", provider="wikipedia") == "wikipedia"
    assert service.select_provider("Python", provider="wikidata") == "wikidata"
    assert service.select_provider("OpenAI", provider="hackernews") == "hackernews"
    assert service.select_provider("transformer", provider="arxiv") == "arxiv"
    assert service.select_provider("large language model", provider="openalex") == "openalex"
    assert service.select_provider("machine learning", provider="crossref") == "crossref"
    assert service.select_provider("cancer", provider="pubmed") == "pubmed"
    assert service.select_provider("transformer", provider="semantic_scholar") == "semantic_scholar"
    assert service.select_provider("python", provider="internet_archive") == "internet_archive"
    assert service.select_provider("example.com", provider="common_crawl") == "common_crawl"
    assert service.select_provider("中文搜索", provider="zhihu") == "zhihu"
    fallback_order = RouterService._provider_order("searxng", allow_fallback=True)
    explicit_only = {
        "duckduckgo",
        "github",
        "stackexchange",
        "wikipedia",
        "wikidata",
        "hackernews",
        "arxiv",
        "openalex",
        "crossref",
        "pubmed",
        "semantic_scholar",
        "internet_archive",
        "common_crawl",
        "zhihu",
    }
    assert explicit_only.isdisjoint(fallback_order)


def test_configured_zhihu_is_selected_for_chinese_auto_search():
    service = RouterService(Settings(gateway_api_key="test", zhihu_api_key="configured"))

    assert service.select_provider("如何优化中文博客 GEO") == "zhihu"
    assert service.provider_configured("zhihu") is True


def test_evidence_chinese_query_prefers_zhihu_without_exceeding_provider_budget():
    service = RouterService(
        Settings(
            gateway_api_key="test",
            zhihu_api_key="configured",
            brave_api_key="configured",
            tavily_api_key="configured",
        )
    )

    assert service.evidence_provider_candidates("如何优化中文博客 GEO")[:2] == ["zhihu", "brave"]


class FakeProvider:
    def __init__(self, results):
        self.results = results

    async def search(self, query, max_results):
        return self.results


def test_auto_falls_back_when_provider_returns_empty_results(monkeypatch):
    service = RouterService(
        Settings(
            gateway_api_key="test",
            grok_search_enabled=True,
            grok_search_auto_enabled=True,
            grok_api_key="gk",
        )
    )
    result = SearchResult(title="fallback", url="https://example.com", snippet="ok")
    service.providers["grok"] = FakeProvider([])
    service.providers["searxng"] = FakeProvider([result])
    monkeypatch.setattr(RouterService, "_provider_order", lambda *args, **kwargs: ["grok", "searxng"])

    import asyncio

    response = asyncio.run(service.search("latest AI news", provider="auto", max_results=1))

    assert response.provider == "searxng"
    assert response.results == [result]


def test_explicit_provider_keeps_empty_results(monkeypatch):
    service = RouterService(Settings(gateway_api_key="test", grok_search_enabled=True, grok_api_key="gk"))
    service.providers["grok"] = FakeProvider([])
    monkeypatch.setattr(RouterService, "_provider_order", lambda *args, **kwargs: ["grok", "searxng"])

    import asyncio

    response = asyncio.run(service.search("agent news", provider="grok", max_results=1))

    assert response == SearchResponse(success=True, provider="grok", query="agent news", cached=False, results=[])


def test_grok_cache_variant_changes_with_backend():
    openai_service = RouterService(
        Settings(gateway_api_key="test", grok_search_enabled=True, grok_backend="openai", grok_api_key="gk")
    )
    hybrid_service = RouterService(
        Settings(gateway_api_key="test", grok_search_enabled=True, grok_backend="hybrid", grok_api_key="gk")
    )

    assert openai_service._cache_variant("grok") != hybrid_service._cache_variant("grok")
    assert openai_service._cache_variant("brave") == hybrid_service._cache_variant("brave")


def test_evidence_auto_candidates_include_only_configured_general_sources():
    service = RouterService(
        Settings(
            gateway_api_key="test",
            brave_api_key="configured",
            tavily_api_key="configured",
            exa_api_key="",
            searxng_enabled=False,
        )
    )

    assert service.evidence_provider_candidates("FastAPI evidence") == ["brave", "tavily"]


def test_evidence_raw_search_skips_legacy_per_provider_rerank(monkeypatch):
    service = RouterService(Settings(gateway_api_key="test"))
    item = SearchResult(title="A", url="https://example.com", snippet="")
    service.providers["brave"] = FakeProvider([item])

    class FakeCache:
        async def get_json(self, key):
            return None

        async def set_json(self, key, value, ttl=None):
            return None

    service.cache = FakeCache()

    async def fail_rerank(*_args, **_kwargs):
        raise AssertionError("raw evidence search must not rerank each provider independently")

    monkeypatch.setattr(service.reranker, "rerank", fail_rerank)

    import asyncio

    response = asyncio.run(service.search_provider("q", "brave", 1, apply_rerank=False))

    assert response.results == [item]
