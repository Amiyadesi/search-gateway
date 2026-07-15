import asyncio

import pytest

from app.config import Settings
from app.providers.firecrawl import ExtractedDocument
from app.schemas.common import SearchResult
from app.schemas.evidence import EvidenceBudget, EvidenceSearchRequest
from app.schemas.search import SearchResponse
from app.services.evidence_service import EvidenceService, RRF_K
from app.services.rerank_service import RerankOutcome
from app.utils.errors import GatewayError


class FakeCache:
    def __init__(self):
        self.values = {}
        self.set_calls = []

    async def get_json(self, key):
        return self.values.get(key)

    async def set_json(self, key, value, ttl=None):
        self.values[key] = value
        self.set_calls.append((key, value, ttl))

    async def delete(self, key):
        self.values.pop(key, None)


class FakeReranker:
    enabled = False

    async def rerank_with_status(self, query, results):
        return RerankOutcome(results=results, applied=False, succeeded=True)


class FakeRouter:
    def __init__(self, responses, candidates=None, reranker=None):
        self.responses = responses
        self.candidates = candidates or ["brave", "tavily"]
        self.cache = FakeCache()
        self.reranker = reranker or FakeReranker()
        self.calls = []

    def evidence_provider_candidates(self, query):
        return self.candidates

    async def search_provider(self, query, provider, limit, apply_rerank=True):
        self.calls.append((query, provider, limit, apply_rerank))
        value = self.responses[(query, provider)]
        if isinstance(value, Exception):
            raise value
        if isinstance(value, SearchResponse):
            return value
        return SearchResponse(
            success=True,
            provider=provider,
            query=query,
            cached=False,
            results=value,
        )

    def _cache_variant(self, provider=""):
        return "test-variant"

    async def close(self):
        return None


def result(title, url, snippet=""):
    return SearchResult(title=title, url=url, snippet=snippet)


def request(**kwargs):
    values = {
        "queries": ["test query"],
        "providers": ["auto"],
        "rerank": False,
        "budget": EvidenceBudget(max_provider_calls=2, max_extract_pages=0, timeout_ms=5000),
    }
    values.update(kwargs)
    return EvidenceSearchRequest(**values)


def test_evidence_merges_two_providers_with_rrf_and_domain_diversity():
    router = FakeRouter(
        {
            ("test query", "brave"): [
                result("Shared", "https://example.com/page?utm_source=test"),
                result("Example B", "https://example.com/b"),
                result("Example C", "https://example.com/c"),
            ],
            ("test query", "tavily"): [
                result("Shared alternate", "https://EXAMPLE.com/page#section"),
                result("Independent", "https://other.org/guide"),
            ],
        }
    )
    service = EvidenceService(Settings(gateway_api_key="test"), router=router)

    response = asyncio.run(service.search(request(max_results=4)))

    assert response.success is True
    assert response.partial is False
    assert response.degraded is False
    assert response.usage.provider_calls == 2
    assert response.results[0].canonical_url == "https://example.com/page"
    assert response.results[0].fusion_score == pytest.approx(2 / (RRF_K + 1))
    assert len(response.results[0].origins) == 2
    assert response.results[0].providers == ["brave", "tavily"]
    assert sum(item.registrable_domain == "example.com" for item in response.results) == 2
    assert any(item.registrable_domain == "other.org" for item in response.results)


def test_evidence_preserves_cross_query_provenance():
    shared = result("Shared", "https://example.com/topic")
    router = FakeRouter(
        {
            ("query one", "brave"): [shared],
            ("query one", "tavily"): [shared],
            ("query two", "brave"): [shared],
            ("query two", "tavily"): [shared],
        }
    )
    service = EvidenceService(Settings(gateway_api_key="test"), router=router)

    response = asyncio.run(service.search(request(queries=["query one", "query two"])))

    assert response.usage.provider_calls == 4
    assert response.results[0].matched_queries == ["query one", "query two"]
    assert len(response.results[0].origins) == 4
    assert response.results[0].fusion_score == pytest.approx(4 / (RRF_K + 1))


def test_evidence_preserves_provider_metadata_only_as_provenance():
    item = SearchResult(
        title="Zhihu result",
        url="https://example.com/topic",
        snippet="Evidence",
        provider_metadata={
            "author_name": "Author",
            "edit_time": 1748355858,
            "authority_level": "2",
            "observed_url": "https://example.com/topic?utm_source=provider",
        },
    )
    router = FakeRouter({("test query", "zhihu"): [item]}, candidates=["zhihu"])
    service = EvidenceService(Settings(gateway_api_key="test"), router=router)

    response = asyncio.run(
        service.search(
            request(
                budget=EvidenceBudget(max_provider_calls=1, max_extract_pages=0, timeout_ms=5000)
            )
        )
    )

    assert response.results[0].canonical_url == "https://example.com/topic"
    assert response.results[0].origins[0].provider_metadata == item.provider_metadata


def test_one_provider_failure_returns_partial_evidence_and_opens_circuit():
    router = FakeRouter(
        {
            ("test query", "brave"): [result("Good", "https://example.com")],
            ("test query", "tavily"): GatewayError(
                "upstream failed",
                status_code=502,
                detail={"status": 429, "retry_after_seconds": 17},
            ),
        }
    )
    service = EvidenceService(Settings(gateway_api_key="test"), router=router)

    response = asyncio.run(service.search(request()))

    assert response.success is True
    assert response.partial is True
    assert response.degraded is True
    assert response.results
    assert response.errors[0].code == "PROVIDER_RATE_LIMITED"
    assert response.errors[0].retryable is True
    assert response.errors[0].retry_after_seconds == 17
    assert router.cache.values["evidence:circuit:tavily"]["code"] == "PROVIDER_RATE_LIMITED"


def test_all_provider_failures_return_structured_unsuccessful_response():
    router = FakeRouter(
        {
            ("test query", "brave"): GatewayError("missing", status_code=500),
            ("test query", "tavily"): asyncio.TimeoutError(),
        }
    )
    service = EvidenceService(Settings(gateway_api_key="test"), router=router)

    response = asyncio.run(service.search(request()))

    assert response.success is False
    assert response.results == []
    assert {item.code for item in response.errors} == {"PROVIDER_UNAVAILABLE", "PROVIDER_TIMEOUT"}
    assert response.usage.successful_provider_calls == 0


def test_successful_empty_sources_are_not_reported_as_failures():
    router = FakeRouter(
        {
            ("test query", "brave"): [],
            ("test query", "tavily"): [],
        }
    )
    service = EvidenceService(Settings(gateway_api_key="test"), router=router)

    response = asyncio.run(service.search(request()))

    assert response.success is True
    assert response.results == []
    assert response.partial is False
    assert response.degraded is False
    assert response.errors == []


def test_auto_selection_never_exceeds_two_sources_per_query():
    router = FakeRouter(
        {
            ("test query", "brave"): [],
            ("test query", "tavily"): [],
            ("test query", "exa"): [],
        },
        candidates=["brave", "tavily", "exa"],
    )
    service = EvidenceService(Settings(gateway_api_key="test"), router=router)

    response = asyncio.run(service.search(request()))

    assert response.usage.provider_calls == 2
    assert [call[1] for call in router.calls] == ["brave", "tavily"]


def test_evidence_response_cache_includes_algorithm_contract():
    router = FakeRouter(
        {
            ("test query", "brave"): [result("Good", "https://example.com")],
        },
        candidates=["brave"],
    )
    service = EvidenceService(Settings(gateway_api_key="test"), router=router)
    payload = request(budget=EvidenceBudget(max_provider_calls=1, max_extract_pages=0, timeout_ms=5000))

    first = asyncio.run(service.search(payload))
    second = asyncio.run(service.search(payload))

    assert first.cached is False
    assert second.cached is True
    assert second.usage.cache_hits == 1
    assert second.usage.provider_calls == 0
    assert second.usage.successful_provider_calls == 0
    assert second.usage.estimated_credits == 0
    assert len(router.calls) == 1
    cache_keys = [item[0] for item in router.cache.set_calls if item[0].startswith("evidence:")]
    assert len(cache_keys) == 1


def test_provider_cache_hit_does_not_report_upstream_calls_or_credits():
    cached_response = SearchResponse(
        success=True,
        provider="brave",
        query="test query",
        cached=True,
        results=[result("Good", "https://example.com")],
    )
    router = FakeRouter({("test query", "brave"): cached_response}, candidates=["brave"])
    service = EvidenceService(Settings(gateway_api_key="test"), router=router)

    response = asyncio.run(
        service.search(
            request(
                budget=EvidenceBudget(max_provider_calls=1, max_extract_pages=0, timeout_ms=5000)
            )
        )
    )

    assert response.success is True
    assert response.usage.provider_calls == 0
    assert response.usage.successful_provider_calls == 0
    assert response.usage.cache_hits == 1
    assert response.usage.estimated_credits == 0


class ConcurrentExtractor:
    def __init__(self, *, same_content=False, same_canonical=False):
        self.active = 0
        self.max_active = 0
        self.same_content = same_content
        self.same_canonical = same_canonical

    async def extract_document(self, url):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        canonical = "https://canonical.example/post" if self.same_canonical else url
        content = "same content" if self.same_content else f"content for {url}"
        return ExtractedDocument(markdown=content, canonical_url=canonical)


def test_extraction_is_bounded_and_records_hashes(monkeypatch):
    urls = [f"https://example{index}.com/page" for index in range(5)]
    router = FakeRouter(
        {("test query", "brave"): [result(str(index), url) for index, url in enumerate(urls)]},
        candidates=["brave"],
    )
    extractor = ConcurrentExtractor()
    settings = Settings(gateway_api_key="test", evidence_extract_concurrency=2)
    service = EvidenceService(settings, router=router, extractor=extractor)
    monkeypatch.setattr("app.services.evidence_service.validate_public_http_url", lambda value: value)
    payload = request(
        max_results=5,
        budget=EvidenceBudget(max_provider_calls=1, max_extract_pages=5, timeout_ms=5000),
    )

    response = asyncio.run(service.search(payload))

    assert extractor.max_active == 2
    assert response.usage.extract_pages == 5
    assert all(item.extract_status == "complete" for item in response.results)
    assert all(item.content_hash and item.content_hash.startswith("sha256:") for item in response.results)


def test_canonical_and_content_hash_deduplication_after_extraction(monkeypatch):
    router = FakeRouter(
        {
            ("test query", "brave"): [
                result("A", "https://one.example/page"),
                result("B", "https://two.example/page"),
            ]
        },
        candidates=["brave"],
    )
    extractor = ConcurrentExtractor(same_content=True, same_canonical=True)
    service = EvidenceService(Settings(gateway_api_key="test"), router=router, extractor=extractor)
    monkeypatch.setattr("app.services.evidence_service.validate_public_http_url", lambda value: value)
    payload = request(
        budget=EvidenceBudget(max_provider_calls=1, max_extract_pages=2, timeout_ms=5000),
    )

    response = asyncio.run(service.search(payload))

    assert len(response.results) == 1
    assert response.results[0].canonical_url == "https://canonical.example/post"
    assert len(response.results[0].origins) == 2
    assert response.results[0].fusion_score == pytest.approx(1 / (RRF_K + 1))


class FailingReranker:
    enabled = True

    async def rerank_with_status(self, query, results):
        return RerankOutcome(results=results, applied=True, succeeded=False, error="timeout")


class SlowReranker:
    enabled = True

    async def rerank_with_status(self, query, results):
        await asyncio.sleep(0.1)
        return RerankOutcome(results=results, applied=True, succeeded=True)


def test_rerank_failure_keeps_rrf_and_marks_degraded():
    router = FakeRouter(
        {
            ("test query", "brave"): [
                result("A", "https://a.example"),
                result("B", "https://b.example"),
            ]
        },
        candidates=["brave"],
        reranker=FailingReranker(),
    )
    service = EvidenceService(Settings(gateway_api_key="test"), router=router)

    response = asyncio.run(service.search(request(rerank=True, budget=EvidenceBudget(max_provider_calls=1, max_extract_pages=0, timeout_ms=5000))))

    assert response.degraded is True
    assert response.errors[0].code == "RERANK_FAILED"
    assert [item.title for item in response.results] == ["A", "B"]


def test_total_deadline_bounds_rerank_and_skips_late_extraction():
    router = FakeRouter(
        {
            ("test query", "brave"): [
                result("A", "https://a.example"),
                result("B", "https://b.example"),
            ]
        },
        candidates=["brave"],
        reranker=SlowReranker(),
    )
    extractor = ConcurrentExtractor()
    service = EvidenceService(Settings(gateway_api_key="test"), router=router, extractor=extractor)
    response = asyncio.run(
        service.search(
            request(
                rerank=True,
                budget=EvidenceBudget.model_construct(
                    max_provider_calls=1,
                    max_extract_pages=2,
                    timeout_ms=50,
                ),
            )
        )
    )

    assert response.degraded is True
    assert {error.code for error in response.errors} >= {"RERANK_TIMEOUT", "REQUEST_DEADLINE_EXCEEDED"}
    assert extractor.max_active == 0
