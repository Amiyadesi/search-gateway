from fastapi import APIRouter, Depends, Query

from app.config import Settings, get_settings
from app.schemas.search import SEARCH_PROVIDER_PATTERN, SearchResponse
from app.services.router_service import RouterService
from app.utils.auth import require_api_key

router = APIRouter(tags=["search"])


@router.get("/search", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=1, max_length=500),
    provider: str = Query(
        "auto",
        pattern=SEARCH_PROVIDER_PATTERN,
    ),
    max_results: int = Query(5, ge=1, le=10),
    _: None = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
) -> SearchResponse:
    service = RouterService(settings)
    try:
        return await service.search(q, provider=provider, max_results=max_results)
    finally:
        await service.close()
