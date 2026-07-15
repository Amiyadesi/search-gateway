from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse

from app.config import Settings, get_settings
from app.schemas.evidence import (
    AnswerModelsRequest,
    AnswerModelsResponse,
    AnswerSnapshotRequest,
    AnswerSnapshotResponse,
    EvidenceSearchRequest,
    EvidenceSearchResponse,
)
from app.services.answer_snapshot_service import AnswerSnapshotService
from app.services.evidence_service import EvidenceService
from app.utils.auth import require_api_key


router = APIRouter(prefix="/v1", tags=["evidence-v1"])


@router.post("/evidence-search", response_model=EvidenceSearchResponse)
async def evidence_search(
    payload: EvidenceSearchRequest,
    _: None = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
) -> EvidenceSearchResponse | JSONResponse:
    service = EvidenceService(settings)
    try:
        response = await service.search(payload)
    finally:
        await service.close()
    if not response.success:
        return JSONResponse(
            status_code=_failure_status(response.errors),
            content=response.model_dump(mode="json"),
        )
    return response


@router.post("/answer-snapshots", response_model=AnswerSnapshotResponse)
async def answer_snapshots(
    payload: AnswerSnapshotRequest,
    x_answer_api_key: str | None = Header(default=None, alias="X-Answer-API-Key"),
    _: None = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
) -> AnswerSnapshotResponse | JSONResponse:
    response = await AnswerSnapshotService(settings).observe(
        payload,
        request_api_key=x_answer_api_key,
    )
    if not response.success:
        return JSONResponse(
            status_code=_failure_status(response.errors),
            content=response.model_dump(mode="json"),
        )
    return response


@router.post("/answer-models", response_model=AnswerModelsResponse)
async def answer_models(
    payload: AnswerModelsRequest,
    x_answer_api_key: str | None = Header(default=None, alias="X-Answer-API-Key"),
    _: None = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
) -> AnswerModelsResponse:
    return await AnswerSnapshotService(settings).list_models(
        payload,
        request_api_key=x_answer_api_key,
    )


def _failure_status(errors: list) -> int:
    return 503 if any(error.retryable for error in errors) else 502
