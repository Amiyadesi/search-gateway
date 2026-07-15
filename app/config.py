from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """集中读取环境变量，Docker 和本地运行共用。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "AI Search Gateway"
    app_env: str = "production"
    log_level: str = "INFO"

    gateway_api_key: str = Field(default="", alias="GATEWAY_API_KEY")

    brave_api_key: str = Field(default="", alias="BRAVE_API_KEY")
    tavily_api_key: str = Field(default="", alias="TAVILY_API_KEY")
    tavily_api_keys: str = Field(default="", alias="TAVILY_API_KEYS")
    tavily_hikari_token: str = Field(default="", alias="TAVILY_HIKARI_TOKEN")
    tavily_hikari_url: str = Field(default="", alias="TAVILY_HIKARI_URL")
    exa_api_key: str = Field(default="", alias="EXA_API_KEY")
    firecrawl_api_key: str = Field(default="", alias="FIRECRAWL_API_KEY")
    apiflash_access_key: str = Field(default="", alias="APIFLASH_ACCESS_KEY")
    apiflash_base_url: str = Field(default="https://api.apiflash.com/v1/urltoimage", alias="APIFLASH_BASE_URL")
    phantomjscloud_api_key: str = Field(default="", alias="PHANTOMJSCLOUD_API_KEY")
    phantomjscloud_base_url: str = Field(default="https://phantomjscloud.com/api/browser/v2", alias="PHANTOMJSCLOUD_BASE_URL")
    screenshotmachine_key: str = Field(default="", alias="SCREENSHOTMACHINE_KEY")
    screenshotmachine_base_url: str = Field(default="https://api.screenshotmachine.com", alias="SCREENSHOTMACHINE_BASE_URL")
    screenshotscout_access_key: str = Field(default="", alias="SCREENSHOTSCOUT_ACCESS_KEY")
    screenshotscout_secret_key: str = Field(default="", alias="SCREENSHOTSCOUT_SECRET_KEY")
    screenshotscout_base_url: str = Field(default="https://api.screenshotscout.com/v1/capture", alias="SCREENSHOTSCOUT_BASE_URL")
    snapapi_api_key: str = Field(default="", alias="SNAPAPI_API_KEY")
    snapapi_base_url: str = Field(default="https://api.snapapi.pics/v1/screenshot", alias="SNAPAPI_BASE_URL")
    screenshotbase_api_key: str = Field(default="", alias="SCREENSHOTBASE_API_KEY")
    screenshotbase_base_url: str = Field(default="https://api.screenshotbase.com/v1/take", alias="SCREENSHOTBASE_BASE_URL")
    thumbnail_ws_api_key: str = Field(default="", alias="THUMBNAIL_WS_API_KEY")
    thumbnail_ws_base_url: str = Field(default="https://api.thumbnail.ws/api", alias="THUMBNAIL_WS_BASE_URL")
    hqapi_screenshot_key: str = Field(default="", alias="HQAPI_SCREENSHOT_KEY")
    hqapi_screenshot_base_url: str = Field(default="https://hqapi.com/api/screenshot", alias="HQAPI_SCREENSHOT_BASE_URL")
    screenshotlayer_access_key: str = Field(default="", alias="SCREENSHOTLAYER_ACCESS_KEY")
    screenshotlayer_base_url: str = Field(default="https://api.screenshotlayer.com/api/capture", alias="SCREENSHOTLAYER_BASE_URL")
    microlink_api_key: str = Field(default="", alias="MICROLINK_API_KEY")
    microlink_base_url: str = Field(default="https://api.microlink.io", alias="MICROLINK_BASE_URL")
    screenshot_provider_order: str = Field(
        default="snapapi,apiflash,microlink,screenshotlayer,phantomjscloud,screenshotbase,screenshotscout,screenshotmachine,thumbnailws,hqapi",
        alias="SCREENSHOT_PROVIDER_ORDER",
    )
    screenshot_timeout_seconds: float = Field(default=45.0, alias="SCREENSHOT_TIMEOUT_SECONDS")
    screenshot_cache_ttl_seconds: int = Field(default=21600, alias="SCREENSHOT_CACHE_TTL_SECONDS")
    screenshot_cache_max_bytes: int = Field(default=3145728, alias="SCREENSHOT_CACHE_MAX_BYTES")
    screenshot_min_markdown_chars: int = Field(default=300, alias="SCREENSHOT_MIN_MARKDOWN_CHARS")
    screenshot_allow_private_urls: bool = Field(default=False, alias="SCREENSHOT_ALLOW_PRIVATE_URLS")
    context7_api_key: str = Field(default="", alias="CONTEXT7_API_KEY")
    context7_base_url: str = Field(default="https://context7.com/api", alias="CONTEXT7_BASE_URL")
    context7_timeout_seconds: float = Field(default=20.0, alias="CONTEXT7_TIMEOUT_SECONDS")
    context7_fast_search: bool = Field(default=True, alias="CONTEXT7_FAST_SEARCH")
    duckduckgo_base_url: str = Field(default="https://api.duckduckgo.com", alias="DUCKDUCKGO_BASE_URL")
    duckduckgo_timeout_seconds: float = Field(default=12.0, alias="DUCKDUCKGO_TIMEOUT_SECONDS")
    github_token: str = Field(default="", alias="GITHUB_TOKEN")
    github_search_base_url: str = Field(default="https://api.github.com/search", alias="GITHUB_SEARCH_BASE_URL")
    github_timeout_seconds: float = Field(default=12.0, alias="GITHUB_TIMEOUT_SECONDS")
    stackexchange_key: str = Field(default="", alias="STACKEXCHANGE_KEY")
    stackexchange_base_url: str = Field(default="https://api.stackexchange.com/2.3", alias="STACKEXCHANGE_BASE_URL")
    stackexchange_site: str = Field(default="stackoverflow", alias="STACKEXCHANGE_SITE")
    stackexchange_timeout_seconds: float = Field(default=12.0, alias="STACKEXCHANGE_TIMEOUT_SECONDS")
    wikipedia_base_url: str = Field(default="https://en.wikipedia.org/w/api.php", alias="WIKIPEDIA_BASE_URL")
    wikipedia_timeout_seconds: float = Field(default=12.0, alias="WIKIPEDIA_TIMEOUT_SECONDS")
    wikidata_base_url: str = Field(default="https://www.wikidata.org/w/api.php", alias="WIKIDATA_BASE_URL")
    wikidata_timeout_seconds: float = Field(default=12.0, alias="WIKIDATA_TIMEOUT_SECONDS")
    open_data_user_agent: str = Field(default="AI-Search-Gateway/1.0", alias="OPEN_DATA_USER_AGENT")
    open_data_contact_email: str = Field(default="", alias="OPEN_DATA_CONTACT_EMAIL")
    hackernews_base_url: str = Field(default="https://hn.algolia.com/api/v1", alias="HACKERNEWS_BASE_URL")
    hackernews_timeout_seconds: float = Field(default=12.0, alias="HACKERNEWS_TIMEOUT_SECONDS")
    arxiv_base_url: str = Field(default="https://export.arxiv.org/api/query", alias="ARXIV_BASE_URL")
    arxiv_timeout_seconds: float = Field(default=15.0, alias="ARXIV_TIMEOUT_SECONDS")
    openalex_base_url: str = Field(default="https://api.openalex.org", alias="OPENALEX_BASE_URL")
    openalex_timeout_seconds: float = Field(default=15.0, alias="OPENALEX_TIMEOUT_SECONDS")
    crossref_base_url: str = Field(default="https://api.crossref.org", alias="CROSSREF_BASE_URL")
    crossref_timeout_seconds: float = Field(default=15.0, alias="CROSSREF_TIMEOUT_SECONDS")
    pubmed_base_url: str = Field(
        default="https://eutils.ncbi.nlm.nih.gov/entrez/eutils",
        alias="PUBMED_BASE_URL",
    )
    pubmed_api_key: str = Field(default="", alias="PUBMED_API_KEY")
    pubmed_timeout_seconds: float = Field(default=15.0, alias="PUBMED_TIMEOUT_SECONDS")
    semantic_scholar_base_url: str = Field(
        default="https://api.semanticscholar.org/graph/v1",
        alias="SEMANTIC_SCHOLAR_BASE_URL",
    )
    semantic_scholar_api_key: str = Field(default="", alias="SEMANTIC_SCHOLAR_API_KEY")
    semantic_scholar_timeout_seconds: float = Field(default=15.0, alias="SEMANTIC_SCHOLAR_TIMEOUT_SECONDS")
    internet_archive_base_url: str = Field(
        default="https://archive.org/advancedsearch.php",
        alias="INTERNET_ARCHIVE_BASE_URL",
    )
    internet_archive_timeout_seconds: float = Field(default=15.0, alias="INTERNET_ARCHIVE_TIMEOUT_SECONDS")
    common_crawl_index_url: str = Field(default="https://index.commoncrawl.org/collinfo.json", alias="COMMON_CRAWL_INDEX_URL")
    common_crawl_timeout_seconds: float = Field(default=20.0, alias="COMMON_CRAWL_TIMEOUT_SECONDS")
    rerank_enabled: bool = Field(default=False, alias="RERANK_ENABLED")
    rerank_base_url: str = Field(default="", alias="RERANK_BASE_URL")
    rerank_api_key: str = Field(default="", alias="RERANK_API_KEY")
    rerank_model: str = Field(default="", alias="RERANK_MODEL")
    rerank_timeout_seconds: float = Field(default=20.0, alias="RERANK_TIMEOUT_SECONDS")
    rerank_top_n: int = Field(default=10, alias="RERANK_TOP_N")
    embedding_base_url: str = Field(default="", alias="EMBEDDING_BASE_URL")
    embedding_api_key: str = Field(default="", alias="EMBEDDING_API_KEY")
    embedding_model: str = Field(default="", alias="EMBEDDING_MODEL")
    ipinfo_enabled: bool = Field(default=False, alias="IPINFO_ENABLED")
    ipinfo_api_key: str = Field(default="", alias="IPINFO_API_KEY")
    ipinfo_base_url: str = Field(default="", alias="IPINFO_BASE_URL")
    ipinfo_timeout_seconds: float = Field(default=12.0, alias="IPINFO_TIMEOUT_SECONDS")
    grok_search_enabled: bool = Field(default=False, alias="GROK_SEARCH_ENABLED")
    grok_search_auto_enabled: bool = Field(default=False, alias="GROK_SEARCH_AUTO_ENABLED")
    grok_backend: Literal["openai", "groksearch", "hybrid"] = Field(default="openai", alias="GROK_BACKEND")
    grok_upstreams: str = Field(default="", alias="GROK_UPSTREAMS")
    grok_base_url: str = Field(default="", alias="GROK_BASE_URL")
    grok_api_key: str = Field(default="", alias="GROK_API_KEY")
    grok_search_model: str = Field(default="grok-4.20-0309-non-reasoning-console", alias="GROK_SEARCH_MODEL")
    grok_search_max_tokens: int = Field(default=1200, alias="GROK_SEARCH_MAX_TOKENS")
    grok_search_timeout_seconds: float = Field(default=90.0, alias="GROK_SEARCH_TIMEOUT_SECONDS")
    groksearch_bridge_url: str = Field(default="http://groksearch-bridge:8010", alias="GROKSEARCH_BRIDGE_URL")
    groksearch_bridge_timeout_seconds: float = Field(default=180.0, alias="GROKSEARCH_BRIDGE_TIMEOUT_SECONDS")
    groksearch_extra_sources: int = Field(default=3, alias="GROKSEARCH_EXTRA_SOURCES")
    searxng_enabled: bool = Field(default=False, alias="SEARXNG_ENABLED")
    searxng_base_url: str = Field(default="http://searxng:8080", alias="SEARXNG_BASE_URL")
    searxng_timeout_seconds: float = Field(default=12.0, alias="SEARXNG_TIMEOUT_SECONDS")

    redis_url: str = Field(default="redis://redis:6379/0", alias="REDIS_URL")
    cache_ttl_seconds: int = Field(default=21600, alias="CACHE_TTL_SECONDS")

    summary_provider: Literal["astrbot", "openai", "deepseek", "custom"] = Field(
        default="astrbot", alias="SUMMARY_PROVIDER"
    )
    summary_model: str = Field(default="mimo-v2.5-pro", alias="SUMMARY_MODEL")
    summary_base_url: str = Field(default="", alias="SUMMARY_BASE_URL")
    summary_api_key: str = Field(default="", alias="SUMMARY_API_KEY")
    summary_context_max_chars: int = Field(default=12000, alias="SUMMARY_CONTEXT_MAX_CHARS")
    summary_max_sources: int = Field(default=3, alias="SUMMARY_MAX_SOURCES")
    summary_model_max_tokens: int = Field(default=900, alias="SUMMARY_MODEL_MAX_TOKENS")
    summary_retry_attempts: int = Field(default=1, alias="SUMMARY_RETRY_ATTEMPTS")
    summary_fallback_enabled: bool = Field(default=True, alias="SUMMARY_FALLBACK_ENABLED")
    summary_user_agent: str = Field(default="Mozilla/5.0", alias="SUMMARY_USER_AGENT")
    summary_accept: str = Field(default="application/json, text/plain, */*", alias="SUMMARY_ACCEPT")

    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")

    request_timeout_seconds: float = Field(default=20.0, alias="REQUEST_TIMEOUT_SECONDS")
    summary_timeout_seconds: float = Field(default=45.0, alias="SUMMARY_TIMEOUT_SECONDS")
    max_search_results: int = Field(default=5, alias="MAX_SEARCH_RESULTS")

    rate_limit_enabled: bool = Field(default=False, alias="RATE_LIMIT_ENABLED")

    @field_validator("summary_base_url")
    @classmethod
    def strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("searxng_base_url")
    @classmethod
    def strip_searxng_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("tavily_hikari_url")
    @classmethod
    def strip_tavily_hikari_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("context7_base_url")
    @classmethod
    def strip_context7_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator(
        "apiflash_base_url",
        "phantomjscloud_base_url",
        "screenshotmachine_base_url",
        "screenshotscout_base_url",
        "snapapi_base_url",
        "screenshotbase_base_url",
        "thumbnail_ws_base_url",
        "hqapi_screenshot_base_url",
        "screenshotlayer_base_url",
        "microlink_base_url",
    )
    @classmethod
    def strip_screenshot_provider_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("duckduckgo_base_url")
    @classmethod
    def strip_duckduckgo_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("github_search_base_url")
    @classmethod
    def strip_github_search_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("stackexchange_base_url")
    @classmethod
    def strip_stackexchange_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator(
        "hackernews_base_url",
        "openalex_base_url",
        "crossref_base_url",
        "pubmed_base_url",
        "semantic_scholar_base_url",
        "rerank_base_url",
        "embedding_base_url",
    )
    @classmethod
    def strip_provider_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("grok_base_url", "groksearch_bridge_url")
    @classmethod
    def strip_grok_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("ipinfo_base_url")
    @classmethod
    def strip_ipinfo_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
