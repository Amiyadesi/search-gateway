from fastapi import APIRouter, Depends

from app.config import Settings, get_settings
from app.schemas.summary import (
    ResearchRequest,
    ResearchResponse,
    SummaryRequest,
    SummaryResponse,
    UrlAnalysisRequest,
    UrlAnalysisResponse,
)
from app.services.summary_service import SummaryService
from app.utils.auth import require_api_key

router = APIRouter(tags=["analysis"])


@router.post("/summary", response_model=SummaryResponse)
async def summary(
    payload: SummaryRequest,
    _: None = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
) -> SummaryResponse:
    service = SummaryService(settings)
    try:
        return await service.summarize(
            payload.query,
            provider=payload.provider,
            max_results=payload.max_results,
            max_sources=payload.max_sources,
            screenshot_mode=payload.screenshot_mode,
        )
    finally:
        await service.close()


@router.post("/analyze-url", response_model=UrlAnalysisResponse)
async def analyze_url(
    payload: UrlAnalysisRequest,
    _: None = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
) -> UrlAnalysisResponse:
    service = SummaryService(settings)
    try:
        return await service.analyze_url(str(payload.url), payload.question, screenshot_mode=payload.screenshot_mode)
    finally:
        await service.close()


@router.post("/research", response_model=ResearchResponse)
async def research(
    payload: ResearchRequest,
    _: None = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
) -> ResearchResponse:
    service = SummaryService(settings)
    try:
        return await service.research(
            payload.query,
            provider=payload.provider,
            max_results=payload.max_results,
            max_sources=payload.max_sources,
            include_markdown=payload.include_markdown,
            screenshot_mode=payload.screenshot_mode,
        )
    finally:
        await service.close()
