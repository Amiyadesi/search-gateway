from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.schemas.search import SEARCH_PROVIDER_PATTERN, SearchResponse
from app.schemas.anysearch import AnySearchOptions, parse_anysearch_params
from app.services.router_service import RouterService
from app.utils.auth import require_api_key
from app.utils.errors import GatewayError

router = APIRouter(tags=["search"])


@router.get("/search", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=1, max_length=500),
    provider: str = Query(
        "auto",
        pattern=SEARCH_PROVIDER_PATTERN,
    ),
    max_results: int = Query(5, ge=1, le=10),
    tag: str | None = Query(None, max_length=120),
    domain: str | None = Query(None, max_length=40),
    sub_domain: str | None = Query(None, max_length=120),
    params: str | None = Query(None, max_length=4000),
    sub_domain_params: str | None = Query(None, max_length=4000),
    zone: str | None = Query(None, max_length=20),
    language: str | None = Query(None, max_length=35),
    _: None = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
) -> SearchResponse:
    service = RouterService(settings)
    try:
        raw_params = params if params is not None else sub_domain_params
        try:
            options = AnySearchOptions(
                tag=tag,
                domain=domain,
                sub_domain=sub_domain,
                params=parse_anysearch_params(raw_params),
                zone=zone,
                language=language,
            )
        except (ValueError, ValidationError) as exc:
            raise GatewayError(str(exc), status_code=422) from exc

        has_options = bool(
            options.tag
            or options.domain
            or options.sub_domain
            or options.params
            or options.zone
            or options.language
        )
        effective_provider = "anysearch" if provider == "auto" and has_options else provider
        provider_options = options.provider_kwargs() if effective_provider == "anysearch" else None
        return await service.search(
            q,
            provider=effective_provider,
            max_results=max_results,
            provider_options=provider_options,
        )
    finally:
        await service.close()
