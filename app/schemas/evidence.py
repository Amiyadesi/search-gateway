from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.search import SEARCH_PROVIDERS


EVIDENCE_VERSION = "1.0.0"
ANSWER_SNAPSHOT_VERSION = "1.0.0"
FailureStage = Literal["discovery", "fetch", "parse", "retrieval", "selection", "attribution"]
AnswerTimeoutPhase = Literal["connect", "write", "read", "pool", "upstream", "gateway", "unknown"]
RunStatus = Literal[
    "complete",
    "empty",
    "timeout",
    "auth_error",
    "rate_limited",
    "upstream_error",
    "invalid_request",
    "unavailable",
    "circuit_open",
]
ExtractStatus = Literal["complete", "not_requested", "blocked", "timeout", "error"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceFilters(StrictModel):
    include_domains: list[str] = Field(default_factory=list, max_length=20)
    exclude_domains: list[str] = Field(default_factory=list, max_length=20)
    freshness: Literal["day", "week", "month", "year"] | None = None

    @field_validator("include_domains", "exclude_domains")
    @classmethod
    def normalize_domains(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            domain = value.strip().lower().strip(".")
            if not domain or "://" in domain or "/" in domain or "@" in domain or len(domain) > 253:
                raise ValueError("domain filters must contain hostnames only")
            try:
                domain = domain.encode("idna").decode("ascii")
            except UnicodeError as exc:
                raise ValueError("domain filter is invalid") from exc
            if domain not in normalized:
                normalized.append(domain)
        return normalized

    @model_validator(mode="after")
    def domains_do_not_conflict(self) -> "EvidenceFilters":
        overlap = set(self.include_domains) & set(self.exclude_domains)
        if overlap:
            raise ValueError("include_domains and exclude_domains must not overlap")
        return self


class EvidenceBudget(StrictModel):
    max_provider_calls: int = Field(
        default=2,
        ge=1,
        le=2,
        description="Maximum providers called for each query.",
    )
    max_extract_pages: int = Field(default=5, ge=0, le=5)
    timeout_ms: int = Field(default=12000, ge=1000, le=30000)


class EvidenceSearchRequest(StrictModel):
    queries: list[str] = Field(min_length=1, max_length=3)
    locale: str = Field(default="en-US", min_length=2, max_length=35, pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
    providers: list[str] = Field(default_factory=lambda: ["auto"], min_length=1, max_length=2)
    max_results: int = Field(default=8, ge=1, le=10)
    filters: EvidenceFilters = Field(default_factory=EvidenceFilters)
    budget: EvidenceBudget = Field(default_factory=EvidenceBudget)
    rerank: bool = True

    @field_validator("queries")
    @classmethod
    def normalize_queries(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            query = " ".join(value.split())
            if not query or len(query) > 500:
                raise ValueError("queries must contain 1-500 characters")
            key = query.casefold()
            if key not in seen:
                seen.add(key)
                normalized.append(query)
        if not normalized:
            raise ValueError("at least one query is required")
        return normalized

    @field_validator("providers")
    @classmethod
    def validate_providers(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            provider = value.strip().lower()
            if provider not in SEARCH_PROVIDERS:
                raise ValueError(f"unsupported provider: {provider}")
            if provider not in normalized:
                normalized.append(provider)
        if "auto" in normalized and len(normalized) != 1:
            raise ValueError("auto cannot be combined with explicit providers")
        return normalized


class EvidenceError(StrictModel):
    code: str
    scope: Literal["request", "provider_run", "extraction", "rerank", "answer_api"]
    stage: FailureStage
    retryable: bool
    message: str
    provider: str | None = None
    query: str | None = None
    source_id: str | None = None
    retry_after_seconds: int | None = None


class AnswerApiError(EvidenceError):
    timeout_phase: AnswerTimeoutPhase | None = None


class EvidenceOrigin(StrictModel):
    query: str
    provider: str
    provider_rank: int = Field(ge=1)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceResult(StrictModel):
    source_id: str
    query: str
    matched_queries: list[str]
    provider: str
    providers: list[str]
    provider_rank: int = Field(ge=1)
    provider_ranks: dict[str, int]
    origins: list[EvidenceOrigin]
    url: str
    canonical_url: str
    registrable_domain: str
    title: str
    snippet: str
    retrieved_at: datetime
    fusion_score: float = Field(ge=0)
    rerank_rank: int | None = Field(default=None, ge=1)
    extract_status: ExtractStatus = "not_requested"
    content: str | None = None
    content_hash: str | None = None


class ProviderRun(StrictModel):
    provider: str
    query: str
    status: RunStatus
    latency_ms: int = Field(ge=0)
    result_count: int = Field(ge=0)
    cache_hit: bool = False
    error: EvidenceError | None = None


class EvidenceUsage(StrictModel):
    provider_calls: int = Field(default=0, ge=0)
    successful_provider_calls: int = Field(default=0, ge=0)
    extract_pages: int = Field(default=0, ge=0)
    cache_hits: int = Field(default=0, ge=0)
    estimated_credits: float | None = Field(default=None, ge=0)
    elapsed_ms: int = Field(default=0, ge=0)


class EvidenceQueryPlan(StrictModel):
    queries: list[str]
    locale: str
    providers: list[str]
    max_results: int
    filters: EvidenceFilters
    budget: EvidenceBudget
    rerank: bool


class EvidenceSearchResponse(StrictModel):
    success: bool = True
    evidence_version: str = EVIDENCE_VERSION
    request_id: str
    requested_at: datetime
    completed_at: datetime
    cached: bool = False
    query_plan: EvidenceQueryPlan
    results: list[EvidenceResult]
    provider_runs: list[ProviderRun]
    usage: EvidenceUsage
    partial: bool
    degraded: bool
    errors: list[EvidenceError]
    limitations: list[str] = Field(default_factory=list)


class AnswerSnapshotRequest(StrictModel):
    queries: list[str] = Field(min_length=1, max_length=3)
    locale: str = Field(default="en-US", min_length=2, max_length=35, pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
    api_base_url: str | None = Field(default=None, max_length=2048)
    api_model: str | None = Field(default=None, max_length=200)

    @field_validator("queries")
    @classmethod
    def normalize_queries(cls, values: list[str]) -> list[str]:
        return EvidenceSearchRequest.normalize_queries(values)

    @field_validator("api_base_url", "api_model", mode="before")
    @classmethod
    def normalize_optional_custom_config(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("api_model")
    @classmethod
    def validate_api_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not sanitize_model_id(value):
            raise ValueError("api_model must be a printable identifier of at most 200 characters")
        return value

    @model_validator(mode="after")
    def custom_config_is_complete(self) -> "AnswerSnapshotRequest":
        if (self.api_base_url is None) != (self.api_model is None):
            raise ValueError("api_base_url and api_model must be provided together")
        return self


class AnswerModelsRequest(StrictModel):
    api_base_url: str = Field(min_length=1, max_length=2048)

    @field_validator("api_base_url")
    @classmethod
    def normalize_api_base_url(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("api_base_url is required")
        return normalized


class AnswerModelsResponse(StrictModel):
    success: bool = True
    models: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("models")
    @classmethod
    def validate_models(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)) or any(sanitize_model_id(value) != value for value in values):
            raise ValueError("models must contain unique sanitized identifiers")
        return values


class AnswerCitation(StrictModel):
    url: str
    title: str = ""
    snippet: str = ""


class AnswerPhaseTiming(StrictModel):
    connect_ms: int | None = Field(default=None, ge=0)
    request_write_ms: int | None = Field(default=None, ge=0)
    upstream_wait_ms: int | None = Field(default=None, ge=0)
    response_read_ms: int | None = Field(default=None, ge=0)
    total_ms: int = Field(ge=0)
    upstream_wait_is_approximation: bool = True


class AnswerObservation(StrictModel):
    query: str
    status: Literal["complete", "error"]
    api_id: str
    model: str
    observed_at: datetime
    latency_ms: int = Field(ge=0)
    answer: str | None = None
    citations: list[AnswerCitation] = Field(default_factory=list)
    usage: dict[str, int | float] = Field(default_factory=dict)
    timing: AnswerPhaseTiming | None = None
    error: AnswerApiError | None = None


class AnswerSnapshotUsage(StrictModel):
    api_calls: int = Field(default=0, ge=0)
    successful_calls: int = Field(default=0, ge=0)
    elapsed_ms: int = Field(default=0, ge=0)
    provider_usage: dict[str, int | float] = Field(default_factory=dict)


class AnswerSnapshotResponse(StrictModel):
    success: bool = True
    snapshot_version: str = ANSWER_SNAPSHOT_VERSION
    request_id: str
    observed_at: datetime
    api_id: str
    model: str
    observations: list[AnswerObservation]
    usage: AnswerSnapshotUsage
    partial: bool
    degraded: bool
    zero_persistence: bool = True
    errors: list[AnswerApiError]
    limitations: list[str]


def sanitize_usage(value: Any) -> dict[str, int | float]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): number
        for key, number in value.items()
        if isinstance(number, (int, float)) and not isinstance(number, bool) and number >= 0
    }


def sanitize_model_id(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    candidate = value.strip()
    if not candidate or len(candidate) > 200:
        return ""
    if any(unicodedata.category(character).startswith("C") for character in candidate):
        return ""
    return candidate
