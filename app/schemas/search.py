from pydantic import BaseModel

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


class SearchResponse(BaseModel):
    success: bool
    provider: str
    query: str
    cached: bool
    results: list[SearchResult]
