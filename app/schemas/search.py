from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import SearchResult


SEARCH_PROVIDERS = [
    "auto",
    "grok",
    "searxng",
    "brave",
    "tavily",
    "tavily_hikari",
    "exa",
    "zhihu",
    "context7",
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
    "serpjet",
]
SEARCH_PROVIDER_PATTERN = "^(" + "|".join(SEARCH_PROVIDERS) + ")$"


class ProviderAttempt(BaseModel):
    provider: str
    status: Literal["success", "empty", "cached", "error"]
    latency_ms: int = Field(ge=0)
    error_type: str | None = None


class SearchResponse(BaseModel):
    success: bool
    provider: str
    query: str
    cached: bool
    results: list[SearchResult]
    fallback_used: bool = False
    provider_attempts: list[ProviderAttempt] = Field(default_factory=list)
