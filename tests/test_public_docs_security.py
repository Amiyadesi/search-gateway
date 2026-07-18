from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app


def test_public_docs_are_read_only_product_introduction():
    response = TestClient(app).get("/docs")

    assert response.status_code == 200
    assert "Search Gateway" in response.text
    assert "Swagger UI" not in response.text
    assert "Try it out" not in response.text
    assert "/openapi.json" not in response.text
    assert "X-API-Key" not in response.text
    assert "search.sayori.org/v1" not in response.text


def test_openapi_schema_requires_gateway_authentication():
    app.dependency_overrides[get_settings] = lambda: Settings(
        gateway_api_key="gateway-secret"
    )

    try:
        client = TestClient(app)
        denied = client.get("/openapi.json")
        allowed = client.get(
            "/openapi.json", headers={"X-API-Key": "gateway-secret"}
        )
    finally:
        app.dependency_overrides.clear()

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["info"]["version"] == "1.2.1"


def test_readiness_requires_gateway_authentication():
    app.dependency_overrides[get_settings] = lambda: Settings(
        gateway_api_key="gateway-secret"
    )

    try:
        response = TestClient(app).get("/readyz")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
