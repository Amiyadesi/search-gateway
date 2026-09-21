import asyncio

from fastapi import APIRouter, Depends, Query

from app.config import Settings, get_settings
from app.providers.anysearch import AnySearchProvider
from app.schemas.anysearch import AnySearchBatchRequest
from app.utils.auth import require_api_key
from app.utils.errors import GatewayError
from app.services.router_service import RouterService


router = APIRouter(prefix="/anysearch", tags=["anysearch"])


@router.get("/sub-domains")
async def sub_domains(
    domain: list[str] = Query(default=[]),
    _: None = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
) -> dict:
    domains = list(dict.fromkeys(item.strip() for item in domain if item.strip()))
    if not 1 <= len(domains) <= 5:
        raise GatewayError("AnySearch domain 数量必须是 1 到 5", status_code=422)
    return {
        "success": True,
        "provider": "anysearch",
        "domains": await AnySearchProvider(settings).sub_domains(domains),
    }


@router.post("/batch-search")
async def batch_search(
    payload: AnySearchBatchRequest,
    _: None = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
) -> dict:
    service = RouterService(settings)
    try:
        responses = await asyncio.gather(
            *(
                service.search(
                    item.query.strip(),
                    provider="anysearch",
                    max_results=item.max_results,
                    provider_options=item.provider_kwargs(),
                )
                for item in payload.queries
            )
        )
        return {
            "success": True,
            "provider": "anysearch",
            "results": [response.model_dump(mode="json") for response in responses],
        }
    finally:
        await service.close()
