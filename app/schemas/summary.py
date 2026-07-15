from pydantic import BaseModel, Field, HttpUrl, field_validator

from app.schemas.common import SearchResult
from app.schemas.search import SEARCH_PROVIDERS
from app.schemas.screenshot import SCREENSHOT_MODES, ScreenshotMetadata


class SummaryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    provider: str = Field(default="auto")
    max_results: int = Field(default=5, ge=1, le=10)
    max_sources: int | None = Field(default=None, ge=1, le=10)
    screenshot_mode: str = Field(default="auto")

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        if value not in SEARCH_PROVIDERS:
            raise ValueError(f"unsupported provider: {value}")
        return value

    @field_validator("screenshot_mode")
    @classmethod
    def validate_screenshot_mode(cls, value: str) -> str:
        if value not in SCREENSHOT_MODES:
            raise ValueError(f"unsupported screenshot mode: {value}")
        return value


class SummaryResponse(BaseModel):
    success: bool
    summary: str
    sources: list[SearchResult]
    screenshots: list[ScreenshotMetadata] = []
    degraded: bool = False
    error: str | None = None


class UrlAnalysisRequest(BaseModel):
    url: HttpUrl
    question: str = Field(default="请总结这个页面的核心内容、关键信息和适合后续检索的要点。", max_length=500)
    screenshot_mode: str = Field(default="auto")

    @field_validator("screenshot_mode")
    @classmethod
    def validate_screenshot_mode(cls, value: str) -> str:
        if value not in SCREENSHOT_MODES:
            raise ValueError(f"unsupported screenshot mode: {value}")
        return value


class UrlAnalysisResponse(BaseModel):
    success: bool
    url: str
    analysis: str
    markdown: str
    screenshot: ScreenshotMetadata | None = None
    degraded: bool = False
    error: str | None = None


class ResearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    provider: str = Field(default="auto")
    max_results: int = Field(default=5, ge=1, le=10)
    max_sources: int = Field(default=3, ge=1, le=10)
    include_markdown: bool = False
    screenshot_mode: str = Field(default="auto")

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        if value not in SEARCH_PROVIDERS:
            raise ValueError(f"unsupported provider: {value}")
        return value

    @field_validator("screenshot_mode")
    @classmethod
    def validate_screenshot_mode(cls, value: str) -> str:
        if value not in SCREENSHOT_MODES:
            raise ValueError(f"unsupported screenshot mode: {value}")
        return value


class ResearchContext(BaseModel):
    title: str
    url: str
    markdown: str
    extracted: bool
    screenshot: ScreenshotMetadata | None = None
    error: str | None = None


class ResearchResponse(BaseModel):
    success: bool
    provider: str
    query: str
    summary: str
    sources: list[SearchResult]
    contexts: list[ResearchContext] = []
    screenshots: list[ScreenshotMetadata] = []
    degraded: bool = False
    error: str | None = None
