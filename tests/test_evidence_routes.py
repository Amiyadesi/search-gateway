from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app
from app.schemas.evidence import (
    AnswerObservation,
    AnswerSnapshotResponse,
    AnswerSnapshotUsage,
    EvidenceBudget,
    EvidenceError,
    EvidenceFilters,
    EvidenceQueryPlan,
    EvidenceSearchResponse,
    EvidenceUsage,
    ProviderRun,
)


def test_openapi_reports_1_2_contract_and_custom_api_guidance():
    schema = app.openapi()

    assert schema["info"]["version"] == "1.2.1"
    answer_route = schema["paths"]["/v1/answer-snapshots"]["post"]
    models_route = schema["paths"]["/v1/answer-models"]["post"]
    assert answer_route["summary"] == "Observe answers from one OpenAI-compatible API"
    assert "/v1" in answer_route["description"]
    assert "/v1" in models_route["description"]


def test_evidence_contract_accepts_zhihu_as_an_explicit_provider():
    payload = EvidenceQueryPlan(
        queries=["中文 GEO"],
        locale="zh-CN",
        providers=["zhihu"],
        max_results=5,
        filters=EvidenceFilters(),
        budget=EvidenceBudget(max_provider_calls=1, max_extract_pages=0),
        rerank=False,
    )

    assert payload.providers == ["zhihu"]


def now():
    return datetime.now(UTC)


def failed_evidence_response():
    error = EvidenceError(
        code="PROVIDER_TIMEOUT",
        scope="provider_run",
        stage="retrieval",
        retryable=True,
        message="timeout",
        provider="brave",
        query="q",
    )
    return EvidenceSearchResponse(
        success=False,
        request_id="evs_test",
        requested_at=now(),
        completed_at=now(),
        query_plan=EvidenceQueryPlan(
            queries=["q"],
            locale="en-US",
            providers=["brave"],
            max_results=8,
            filters=EvidenceFilters(),
            budget=EvidenceBudget(max_extract_pages=0),
            rerank=False,
        ),
        results=[],
        provider_runs=[
            ProviderRun(
                provider="brave",
                query="q",
                status="timeout",
                latency_ms=10,
                result_count=0,
                error=error,
            )
        ],
        usage=EvidenceUsage(provider_calls=1, elapsed_ms=10),
        partial=True,
        degraded=True,
        errors=[error],
    )


def test_evidence_route_returns_structured_503(monkeypatch):
    class FakeService:
        def __init__(self, settings):
            pass

        async def search(self, payload):
            return failed_evidence_response()

        async def close(self):
            return None

    monkeypatch.setattr("app.routes.evidence.EvidenceService", FakeService)
    app.dependency_overrides[get_settings] = lambda: Settings(gateway_api_key="gateway-test")
    try:
        response = TestClient(app).post(
            "/v1/evidence-search",
            headers={"X-API-Key": "gateway-test"},
            json={"queries": ["q"], "providers": ["brave"], "budget": {"max_extract_pages": 0}},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["errors"][0]["code"] == "PROVIDER_TIMEOUT"
    assert "timeout_phase" not in response.json()["errors"][0]


def test_answer_snapshot_route_passes_key_only_to_request_service(monkeypatch):
    captured = {}

    class FakeService:
        def __init__(self, settings):
            pass

        async def observe(self, payload, request_api_key=None):
            captured["key"] = request_api_key
            observation = AnswerObservation(
                query="q",
                status="complete",
                api_id="configured_api",
                model="fixed-model",
                observed_at=now(),
                latency_ms=1,
                answer="answer",
            )
            return AnswerSnapshotResponse(
                request_id="ans_test",
                observed_at=now(),
                api_id="configured_api",
                model="fixed-model",
                observations=[observation],
                usage=AnswerSnapshotUsage(api_calls=1, successful_calls=1, elapsed_ms=1),
                partial=False,
                degraded=False,
                errors=[],
                limitations=["API observation only"],
            )

    monkeypatch.setattr("app.routes.evidence.AnswerSnapshotService", FakeService)
    app.dependency_overrides[get_settings] = lambda: Settings(gateway_api_key="gateway-test")
    try:
        response = TestClient(app).post(
            "/v1/answer-snapshots",
            headers={"X-API-Key": "gateway-test", "X-Answer-API-Key": "request-secret"},
            json={"queries": ["q"]},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert captured["key"] == "request-secret"
    assert "request-secret" not in response.text


def test_answer_models_route_returns_sanitized_ids(monkeypatch):
    captured = {}

    class FakeService:
        def __init__(self, settings):
            pass

        async def list_models(self, payload, request_api_key=None):
            captured["base_url"] = payload.api_base_url
            captured["key"] = request_api_key
            from app.schemas.evidence import AnswerModelsResponse

            return AnswerModelsResponse(models=["model-a", "model-b"])

    monkeypatch.setattr("app.routes.evidence.AnswerSnapshotService", FakeService)
    app.dependency_overrides[get_settings] = lambda: Settings(gateway_api_key="gateway-test")
    try:
        response = TestClient(app).post(
            "/v1/answer-models",
            headers={"X-API-Key": "gateway-test", "X-Answer-API-Key": "request-secret"},
            json={"api_base_url": "https://api.public-service.com/v1"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"success": True, "models": ["model-a", "model-b"]}
    assert captured["key"] == "request-secret"
    assert "request-secret" not in response.text


def test_answer_models_route_requires_request_key():
    app.dependency_overrides[get_settings] = lambda: Settings(gateway_api_key="gateway-test")
    try:
        response = TestClient(app).post(
            "/v1/answer-models",
            headers={"X-API-Key": "gateway-test"},
            json={"api_base_url": "https://api.public-service.com/v1"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["code"] == "ANSWER_API_KEY_REQUIRED"


def test_answer_models_route_rejects_unsafe_url_without_echoing_it():
    app.dependency_overrides[get_settings] = lambda: Settings(gateway_api_key="gateway-test")
    try:
        response = TestClient(app).post(
            "/v1/answer-models",
            headers={"X-API-Key": "gateway-test", "X-Answer-API-Key": "request-secret"},
            json={"api_base_url": "http://127.0.0.1:8080/v1?secret=value"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["code"] == "ANSWER_API_URL_INVALID"
    assert "127.0.0.1" not in response.text
    assert "request-secret" not in response.text


def test_answer_snapshot_partial_custom_config_is_rejected_without_echoing_url():
    app.dependency_overrides[get_settings] = lambda: Settings(gateway_api_key="gateway-test")
    try:
        response = TestClient(app).post(
            "/v1/answer-snapshots",
            headers={"X-API-Key": "gateway-test", "X-Answer-API-Key": "request-secret"},
            json={"queries": ["q"], "api_base_url": "https://sensitive.example/v1"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert "sensitive.example" not in response.text
    assert "request-secret" not in response.text


def test_validation_errors_do_not_echo_misplaced_secret_fields():
    app.dependency_overrides[get_settings] = lambda: Settings(gateway_api_key="gateway-test")
    try:
        response = TestClient(app).post(
            "/v1/answer-snapshots",
            headers={"X-API-Key": "gateway-test"},
            json={"queries": ["q"], "api_key": "misplaced-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert "misplaced-secret" not in response.text
    assert response.json()["detail"][0].keys() <= {"type", "loc", "msg"}
