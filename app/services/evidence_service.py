from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from app.config import Settings
from app.providers.firecrawl import ExtractedDocument, FirecrawlProvider
from app.schemas.common import SearchResult
from app.schemas.evidence import (
    EVIDENCE_VERSION,
    EvidenceError,
    EvidenceOrigin,
    EvidenceQueryPlan,
    EvidenceResult,
    EvidenceSearchRequest,
    EvidenceSearchResponse,
    EvidenceUsage,
    ProviderRun,
)
from app.services.router_service import RouterService
from app.utils.errors import GatewayError
from app.utils.url_normalization import (
    content_hash,
    domain_matches,
    normalize_url,
    registrable_domain,
    source_id,
    validate_public_http_url,
)


RRF_K = 60


@dataclass
class _Candidate:
    url: str
    canonical_url: str
    title: str
    snippet: str
    retrieved_at: datetime
    origins: list[EvidenceOrigin] = field(default_factory=list)
    fusion_score: float = 0.0
    rerank_rank: int | None = None
    extract_status: str = "not_requested"
    content: str | None = None
    content_hash: str | None = None


@dataclass(frozen=True)
class _ProviderOutcome:
    run: ProviderRun
    results: list[SearchResult]


@dataclass(frozen=True)
class _ExtractionOutcome:
    canonical_url: str
    status: str
    content: str | None
    content_hash: str | None
    attempted: bool
    error: EvidenceError | None


class EvidenceService:
    def __init__(
        self,
        settings: Settings,
        *,
        router: RouterService | None = None,
        extractor: FirecrawlProvider | None = None,
    ) -> None:
        self.settings = settings
        self.router = router or RouterService(settings)
        self.extractor = extractor or FirecrawlProvider(settings)

    async def search(self, request: EvidenceSearchRequest) -> EvidenceSearchResponse:
        started = time.perf_counter()
        cached = await self.router.cache.get_json(self._cache_key(request))
        if cached is not None:
            response = EvidenceSearchResponse(**cached)
            response.cached = True
            response.usage = EvidenceUsage(
                provider_calls=0,
                successful_provider_calls=0,
                extract_pages=0,
                cache_hits=1,
                estimated_credits=0.0,
                elapsed_ms=max(0, round((time.perf_counter() - started) * 1000)),
            )
            return response

        requested_at = datetime.now(UTC)
        deadline = time.monotonic() + request.budget.timeout_ms / 1000
        skipped_runs: list[ProviderRun] = []
        provider_jobs: list[tuple[str, str]] = []
        for query in request.queries:
            providers, skipped = await self._providers_for_query(query, request)
            provider_jobs.extend((query, provider) for provider in providers)
            skipped_runs.extend(skipped)

        outcomes = await asyncio.gather(
            *(self._run_provider(query, provider, request, deadline) for query, provider in provider_jobs)
        )
        provider_runs = skipped_runs + [outcome.run for outcome in outcomes]
        provider_runs.sort(key=lambda item: (request.queries.index(item.query), item.provider))

        errors = [run.error for run in provider_runs if run.error is not None]
        candidates, parse_errors = self._fuse(outcomes, request)
        errors.extend(parse_errors)

        rerank_error = await self._rerank(candidates, request, deadline)
        if rerank_error:
            errors.append(rerank_error)

        extraction_errors: list[EvidenceError] = []
        extract_pages = 0
        extraction_limit = min(request.budget.max_extract_pages, len(candidates))
        if extraction_limit and time.monotonic() < deadline:
            semaphore = asyncio.Semaphore(max(1, self.settings.evidence_extract_concurrency))
            extraction_outcomes = await asyncio.gather(
                *(
                    self._extract(candidate, semaphore, deadline)
                    for candidate in candidates[:extraction_limit]
                )
            )
            for candidate, outcome in zip(candidates[:extraction_limit], extraction_outcomes, strict=True):
                candidate.canonical_url = outcome.canonical_url or candidate.canonical_url
                candidate.extract_status = outcome.status
                candidate.content = outcome.content
                candidate.content_hash = outcome.content_hash
                extract_pages += int(outcome.attempted)
                if outcome.error:
                    extraction_errors.append(outcome.error)
            errors.extend(extraction_errors)
        elif extraction_limit:
            errors.append(
                EvidenceError(
                    code="REQUEST_DEADLINE_EXCEEDED",
                    scope="request",
                    stage="fetch",
                    retryable=True,
                    message="The evidence request deadline was reached before extraction could start.",
                )
            )

        candidates = self._merge_after_extraction(candidates)
        results = self._select_results(candidates, request.max_results)
        successful_runs = sum(run.status in {"complete", "empty"} for run in provider_runs)
        cache_hits = sum(run.cache_hit for run in provider_runs)
        upstream_calls = sum(not outcome.run.cache_hit for outcome in outcomes)
        successful_upstream_calls = sum(
            outcome.run.status in {"complete", "empty"} and not outcome.run.cache_hit
            for outcome in outcomes
        )
        provider_failures = [run for run in provider_runs if run.status not in {"complete", "empty"}]
        all_failed = bool(provider_runs) and successful_runs == 0
        partial = bool(provider_failures or parse_errors or extraction_errors or rerank_error)
        completed_at = datetime.now(UTC)
        response = EvidenceSearchResponse(
            success=not all_failed,
            request_id=f"evs_{secrets.token_hex(8)}",
            requested_at=requested_at,
            completed_at=completed_at,
            query_plan=EvidenceQueryPlan(
                queries=request.queries,
                locale=request.locale,
                providers=request.providers,
                max_results=request.max_results,
                filters=request.filters,
                budget=request.budget,
                rerank=request.rerank,
            ),
            results=results,
            provider_runs=provider_runs,
            usage=EvidenceUsage(
                provider_calls=upstream_calls,
                successful_provider_calls=successful_upstream_calls,
                extract_pages=extract_pages,
                cache_hits=cache_hits,
                estimated_credits=float(upstream_calls),
                elapsed_ms=max(0, round((time.perf_counter() - started) * 1000)),
            ),
            partial=partial,
            degraded=bool(errors),
            errors=errors,
            limitations=self._limitations(request),
        )
        if response.success and not response.degraded:
            await self.router.cache.set_json(
                self._cache_key(request),
                response.model_dump(mode="json"),
                ttl=self.settings.evidence_cache_ttl_seconds,
            )
        return response

    async def close(self) -> None:
        await self.router.close()

    async def _providers_for_query(
        self,
        query: str,
        request: EvidenceSearchRequest,
    ) -> tuple[list[str], list[ProviderRun]]:
        auto = request.providers == ["auto"]
        candidates = self.router.evidence_provider_candidates(query) if auto else request.providers
        selected: list[str] = []
        skipped: list[ProviderRun] = []
        for provider in candidates:
            circuit = await self.router.cache.get_json(self._circuit_key(provider))
            if circuit:
                retry_after = max(1, round(float(circuit.get("until", time.time())) - time.time()))
                error = EvidenceError(
                    code="PROVIDER_CIRCUIT_OPEN",
                    scope="provider_run",
                    stage="retrieval",
                    retryable=True,
                    message="The evidence source is temporarily unavailable.",
                    provider=provider,
                    query=query,
                    retry_after_seconds=retry_after,
                )
                skipped.append(
                    ProviderRun(
                        provider=provider,
                        query=query,
                        status="circuit_open",
                        latency_ms=0,
                        result_count=0,
                        error=error,
                    )
                )
                if not auto:
                    continue
            else:
                selected.append(provider)
            if len(selected) >= request.budget.max_provider_calls:
                break
        return selected, skipped

    async def _run_provider(
        self,
        query: str,
        provider: str,
        request: EvidenceSearchRequest,
        deadline: float,
    ) -> _ProviderOutcome:
        started = time.perf_counter()
        try:
            timeout = max(0.001, deadline - time.monotonic())
            response = await asyncio.wait_for(
                self.router.search_provider(
                    query,
                    provider,
                    min(10, max(request.max_results, request.max_results * 2)),
                    apply_rerank=False,
                ),
                timeout=timeout,
            )
            await self.router.cache.delete(self._circuit_key(provider))
            status = "complete" if response.results else "empty"
            return _ProviderOutcome(
                run=ProviderRun(
                    provider=provider,
                    query=query,
                    status=status,
                    latency_ms=max(0, round((time.perf_counter() - started) * 1000)),
                    result_count=len(response.results),
                    cache_hit=response.cached,
                ),
                results=response.results,
            )
        except Exception as exc:
            status, error, cooldown = self._classify_provider_error(exc, provider, query)
            if cooldown:
                await self.router.cache.set_json(
                    self._circuit_key(provider),
                    {"code": error.code, "until": time.time() + cooldown},
                    ttl=cooldown,
                )
            return _ProviderOutcome(
                run=ProviderRun(
                    provider=provider,
                    query=query,
                    status=status,
                    latency_ms=max(0, round((time.perf_counter() - started) * 1000)),
                    result_count=0,
                    error=error,
                ),
                results=[],
            )

    def _fuse(
        self,
        outcomes: list[_ProviderOutcome],
        request: EvidenceSearchRequest,
    ) -> tuple[list[_Candidate], list[EvidenceError]]:
        candidates: dict[str, _Candidate] = {}
        errors: list[EvidenceError] = []
        for outcome in outcomes:
            if outcome.run.status != "complete":
                continue
            seen_in_run: set[str] = set()
            for rank, item in enumerate(outcome.results, start=1):
                normalized = normalize_url(str(item.url))
                if not normalized:
                    errors.append(
                        EvidenceError(
                            code="INVALID_RESULT_URL",
                            scope="provider_run",
                            stage="parse",
                            retryable=False,
                            message="A search result contained an invalid URL and was skipped.",
                            provider=outcome.run.provider,
                            query=outcome.run.query,
                        )
                    )
                    continue
                if normalized in seen_in_run:
                    continue
                seen_in_run.add(normalized)
                host = urlsplit(normalized).hostname or ""
                root = registrable_domain(host)
                if request.filters.include_domains and not any(
                    domain_matches(host, domain) or root == registrable_domain(domain)
                    for domain in request.filters.include_domains
                ):
                    continue
                if any(
                    domain_matches(host, domain) or root == registrable_domain(domain)
                    for domain in request.filters.exclude_domains
                ):
                    continue

                origin = EvidenceOrigin(
                    query=outcome.run.query,
                    provider=outcome.run.provider,
                    provider_rank=rank,
                    provider_metadata=item.provider_metadata,
                )
                candidate = candidates.get(normalized)
                if candidate is None:
                    candidate = _Candidate(
                        url=normalized,
                        canonical_url=normalized,
                        title=item.title,
                        snippet=item.snippet,
                        retrieved_at=datetime.now(UTC),
                    )
                    candidates[normalized] = candidate
                candidate.origins.append(origin)
                candidate.fusion_score += 1 / (RRF_K + rank)
                if rank < min((value.provider_rank for value in candidate.origins[:-1]), default=10_000):
                    candidate.title = item.title
                    candidate.snippet = item.snippet

        ordered = sorted(
            candidates.values(),
            key=lambda item: (-item.fusion_score, self._best_rank(item), item.canonical_url),
        )
        return ordered[: max(request.max_results * 3, request.max_results)], errors

    async def _rerank(
        self,
        candidates: list[_Candidate],
        request: EvidenceSearchRequest,
        deadline: float,
    ) -> EvidenceError | None:
        if not request.rerank or len(candidates) <= 1 or not self.router.reranker.enabled:
            return None
        search_results = [
            SearchResult(title=item.title, url=item.canonical_url, snippet=item.snippet)
            for item in candidates[:20]
        ]
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return EvidenceError(
                code="RERANK_TIMEOUT",
                scope="rerank",
                stage="selection",
                retryable=True,
                message="Reranking was skipped because the evidence request deadline was reached.",
            )
        try:
            outcome = await asyncio.wait_for(
                self.router.reranker.rerank_with_status(" | ".join(request.queries), search_results),
                timeout=remaining,
            )
        except (TimeoutError, asyncio.TimeoutError):
            return EvidenceError(
                code="RERANK_TIMEOUT",
                scope="rerank",
                stage="selection",
                retryable=True,
                message="Reranking exceeded the evidence request deadline; the deterministic RRF order was retained.",
            )
        if not outcome.succeeded:
            return EvidenceError(
                code="RERANK_FAILED",
                scope="rerank",
                stage="selection",
                retryable=True,
                message="Reranking failed; the deterministic RRF order was retained.",
            )
        order = {normalize_url(str(item.url)): rank for rank, item in enumerate(outcome.results, start=1)}
        for candidate in candidates:
            candidate.rerank_rank = order.get(candidate.canonical_url)
        candidates.sort(
            key=lambda item: (
                item.rerank_rank if item.rerank_rank is not None else 1_000_000,
                -item.fusion_score,
                item.canonical_url,
            )
        )
        return None

    async def _extract(
        self,
        candidate: _Candidate,
        semaphore: asyncio.Semaphore,
        deadline: float,
    ) -> _ExtractionOutcome:
        try:
            target = validate_public_http_url(candidate.canonical_url)
        except GatewayError:
            error = EvidenceError(
                code="EXTRACTION_URL_BLOCKED",
                scope="extraction",
                stage="fetch",
                retryable=False,
                message="The result URL is not an allowed public HTTP(S) target.",
                source_id=source_id(candidate.canonical_url),
            )
            return _ExtractionOutcome(candidate.canonical_url, "blocked", None, None, False, error)

        try:
            async with semaphore:
                timeout = max(0.001, deadline - time.monotonic())
                document: ExtractedDocument = await asyncio.wait_for(
                    self.extractor.extract_document(target),
                    timeout=timeout,
                )
            canonical = normalize_url(document.canonical_url, base_url=target) or target
            digest = content_hash(document.markdown)
            content = document.markdown[: max(1, self.settings.evidence_max_content_chars)]
            return _ExtractionOutcome(canonical, "complete", content, digest, True, None)
        except (TimeoutError, asyncio.TimeoutError):
            error = EvidenceError(
                code="EXTRACTION_TIMEOUT",
                scope="extraction",
                stage="fetch",
                retryable=True,
                message="Page extraction exceeded the request deadline.",
                source_id=source_id(candidate.canonical_url),
            )
            return _ExtractionOutcome(candidate.canonical_url, "timeout", None, None, True, error)
        except Exception as exc:
            retryable = not (isinstance(exc, GatewayError) and exc.status_code in {400, 401, 403, 422})
            error = EvidenceError(
                code="EXTRACTION_FAILED",
                scope="extraction",
                stage="fetch",
                retryable=retryable,
                message="Page extraction failed; search metadata remains available.",
                source_id=source_id(candidate.canonical_url),
            )
            return _ExtractionOutcome(candidate.canonical_url, "error", None, None, True, error)

    def _merge_after_extraction(self, candidates: list[_Candidate]) -> list[_Candidate]:
        canonical_map: dict[str, _Candidate] = {}
        hash_map: dict[str, _Candidate] = {}
        for candidate in candidates:
            existing = canonical_map.get(candidate.canonical_url)
            if existing is None and candidate.content_hash:
                existing = hash_map.get(candidate.content_hash)
            if existing is None:
                canonical_map[candidate.canonical_url] = candidate
                if candidate.content_hash:
                    hash_map[candidate.content_hash] = candidate
                continue
            self._merge_candidate(existing, candidate)

        merged = list(canonical_map.values())
        merged.sort(
            key=lambda item: (
                item.rerank_rank if item.rerank_rank is not None else 1_000_000,
                -item.fusion_score,
                self._best_rank(item),
                item.canonical_url,
            )
        )
        return merged

    @classmethod
    def _merge_candidate(cls, target: _Candidate, source: _Candidate) -> None:
        known = {(item.query, item.provider, item.provider_rank) for item in target.origins}
        target.origins.extend(
            item
            for item in source.origins
            if (item.query, item.provider, item.provider_rank) not in known
        )
        target.fusion_score = cls._rrf_score(target.origins)
        if target.content is None and source.content is not None:
            target.content = source.content
            target.content_hash = source.content_hash
            target.extract_status = source.extract_status
        if source.rerank_rank is not None:
            target.rerank_rank = min(target.rerank_rank or source.rerank_rank, source.rerank_rank)

    def _select_results(self, candidates: list[_Candidate], max_results: int) -> list[EvidenceResult]:
        selected: list[EvidenceResult] = []
        per_domain: dict[str, int] = {}
        for candidate in candidates:
            root = registrable_domain(candidate.canonical_url)
            if per_domain.get(root, 0) >= 2:
                continue
            per_domain[root] = per_domain.get(root, 0) + 1
            origins = sorted(
                candidate.origins,
                key=lambda item: (item.provider_rank, item.query, item.provider),
            )
            primary = origins[0]
            providers = list(dict.fromkeys(item.provider for item in origins))
            queries = list(dict.fromkeys(item.query for item in origins))
            provider_ranks = {
                provider: min(item.provider_rank for item in origins if item.provider == provider)
                for provider in providers
            }
            selected.append(
                EvidenceResult(
                    source_id=source_id(candidate.canonical_url),
                    query=primary.query,
                    matched_queries=queries,
                    provider=primary.provider,
                    providers=providers,
                    provider_rank=primary.provider_rank,
                    provider_ranks=provider_ranks,
                    origins=origins,
                    url=candidate.url,
                    canonical_url=candidate.canonical_url,
                    registrable_domain=root,
                    title=candidate.title,
                    snippet=candidate.snippet,
                    retrieved_at=candidate.retrieved_at,
                    fusion_score=round(candidate.fusion_score, 12),
                    rerank_rank=candidate.rerank_rank,
                    extract_status=candidate.extract_status,
                    content=candidate.content,
                    content_hash=candidate.content_hash,
                )
            )
            if len(selected) >= max_results:
                break
        return selected

    def _classify_provider_error(
        self,
        exc: Exception,
        provider: str,
        query: str,
    ) -> tuple[str, EvidenceError, int]:
        upstream_status = None
        if isinstance(exc, GatewayError) and isinstance(exc.detail, dict):
            upstream_status = exc.detail.get("status")
        effective_status = upstream_status or (exc.status_code if isinstance(exc, GatewayError) else None)
        if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) or (
            isinstance(exc, GatewayError) and exc.status_code == 504
        ):
            status, code, retryable = "timeout", "PROVIDER_TIMEOUT", True
            cooldown = self.settings.evidence_error_cooldown_seconds
        elif effective_status in {401, 403}:
            status, code, retryable = "auth_error", "PROVIDER_AUTH_ERROR", False
            cooldown = self.settings.evidence_auth_cooldown_seconds
        elif effective_status in {402, 429}:
            status, code, retryable = "rate_limited", "PROVIDER_RATE_LIMITED", True
            retry_after = exc.detail.get("retry_after_seconds") if isinstance(exc, GatewayError) else None
            cooldown = (
                max(1, min(86400, int(retry_after)))
                if isinstance(retry_after, (int, float))
                else self.settings.evidence_rate_limit_cooldown_seconds
            )
        elif isinstance(exc, GatewayError) and exc.status_code in {400, 422}:
            status, code, retryable = "invalid_request", "PROVIDER_INVALID_REQUEST", False
            cooldown = 0
        elif isinstance(exc, GatewayError) and exc.status_code == 500:
            status, code, retryable = "unavailable", "PROVIDER_UNAVAILABLE", False
            cooldown = self.settings.evidence_auth_cooldown_seconds
        else:
            status, code, retryable = "upstream_error", "PROVIDER_UPSTREAM_ERROR", True
            cooldown = self.settings.evidence_error_cooldown_seconds
        error = EvidenceError(
            code=code,
            scope="provider_run",
            stage="retrieval",
            retryable=retryable,
            message=self._provider_error_message(status),
            provider=provider,
            query=query,
            retry_after_seconds=cooldown or None,
        )
        return status, error, max(0, cooldown)

    @staticmethod
    def _provider_error_message(status: str) -> str:
        return {
            "timeout": "The evidence source timed out.",
            "auth_error": "The evidence source rejected its server-side credentials.",
            "rate_limited": "The evidence source is temporarily rate limited.",
            "invalid_request": "The evidence source rejected the normalized query.",
            "unavailable": "The evidence source is not configured or available.",
        }.get(status, "The evidence source returned an upstream error.")

    @staticmethod
    def _best_rank(candidate: _Candidate) -> int:
        return min((item.provider_rank for item in candidate.origins), default=1_000_000)

    @staticmethod
    def _rrf_score(origins: list[EvidenceOrigin]) -> float:
        best_ranks: dict[tuple[str, str], int] = {}
        for origin in origins:
            key = (origin.query, origin.provider)
            best_ranks[key] = min(best_ranks.get(key, origin.provider_rank), origin.provider_rank)
        return sum(1 / (RRF_K + rank) for rank in best_ranks.values())

    def _cache_key(self, request: EvidenceSearchRequest) -> str:
        material: dict[str, Any] = {
            "algorithm": EVIDENCE_VERSION,
            "request": request.model_dump(mode="json"),
            "rerank": self.router._cache_variant(),
        }
        digest = hashlib.sha256(
            json.dumps(material, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return f"evidence:{digest}"

    @staticmethod
    def _circuit_key(provider: str) -> str:
        return f"evidence:circuit:{provider}"

    @staticmethod
    def _limitations(request: EvidenceSearchRequest) -> list[str]:
        limitations = [
            "These are dated search and page observations, not proof of citation by a consumer AI interface.",
            "Provider ranking, indexing, locale, and availability can change after this snapshot.",
        ]
        if request.filters.freshness:
            limitations.append(
                "Freshness is recorded in the query plan and cache identity; enforcement depends on the selected search source."
            )
        return limitations
