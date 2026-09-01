from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app
from app.oauth import issue_access_token, verify_access_token
from app.routes import mcp


def _settings() -> Settings:
    return Settings(
        mcp_access_token="legacy-secret",
        mcp_oauth_issuer="https://gateway.sayori.org",
        mcp_oauth_login_username="owner",
        mcp_oauth_login_secret="login-secret",
        mcp_oauth_signing_secret="signing-secret",
    )


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
    return verifier, challenge


def test_oauth_metadata_advertises_mcp_authorization():
    app.dependency_overrides[get_settings] = _settings
    try:
        client = TestClient(app)
        protected = client.get("/.well-known/oauth-protected-resource")
        metadata = client.get("/.well-known/oauth-authorization-server")
    finally:
        app.dependency_overrides.clear()

    assert protected.status_code == 200
    assert protected.json()["resource"] == "https://gateway.sayori.org"
    assert metadata.json()["authorization_endpoint"] == "https://gateway.sayori.org/oauth/authorize"
    assert metadata.json()["code_challenge_methods_supported"] == ["S256"]
    assert metadata.json()["token_endpoint_auth_methods_supported"] == ["none"]
    assert metadata.json()["scopes_supported"] == [
        "mcp",
        "notes:read",
        "notes:write",
        "files:read",
        "files:write",
        "vaults:read",
    ]


def test_oauth_dcr_pkce_and_one_shot_code_exchange():
    app.dependency_overrides[get_settings] = _settings
    try:
        client = TestClient(app)
        registration = client.post(
            "/oauth/register",
            json={
                "client_name": "ChatGPT",
                "redirect_uris": ["https://chatgpt.com/connector_platform_oauth_redirect"],
                "token_endpoint_auth_method": "none",
            },
        )
        assert registration.status_code == 201
        client_id = registration.json()["client_id"]
        verifier, challenge = _pkce()
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": "https://chatgpt.com/connector_platform_oauth_redirect",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": "https://gateway.sayori.org/mcp/fns",
            "scope": "mcp",
            "state": "state-1",
        }
        login = client.get("/oauth/authorize", params=params)
        assert login.status_code == 200
        assert "Sayori MCP" in login.text

        approved = client.post(
            "/oauth/authorize",
            data={**params, "username": "owner", "password": "login-secret"},
            follow_redirects=False,
        )
        assert approved.status_code == 302
        callback = parse_qs(urlparse(approved.headers["location"]).query)
        assert callback["state"] == ["state-1"]
        assert callback["iss"] == ["https://gateway.sayori.org"]
        code = callback["code"][0]

        exchanged = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": code,
                "redirect_uri": params["redirect_uri"],
                "code_verifier": verifier,
                "resource": params["resource"],
            },
        )
        assert exchanged.status_code == 200
        token = exchanged.json()["access_token"]
        claims = verify_access_token(
            token,
            secret="signing-secret",
            issuer="https://gateway.sayori.org",
            resource="https://gateway.sayori.org/mcp/fns",
        )
        assert claims["aud"] == params["resource"]
        assert claims["scope"] == "mcp"

        reused = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": code,
                "redirect_uri": params["redirect_uri"],
                "code_verifier": verifier,
                "resource": params["resource"],
            },
        )
        assert reused.status_code == 400
        assert reused.json()["error"] == "invalid_grant"
    finally:
        app.dependency_overrides.clear()


def test_oauth_preserves_fns_granular_scopes_and_rejects_unknown_scope():
    app.dependency_overrides[get_settings] = _settings
    try:
        client = TestClient(app)
        registration = client.post(
            "/oauth/register",
            json={
                "client_name": "ChatGPT",
                "redirect_uris": ["https://chatgpt.com/connector_platform_oauth_redirect"],
            },
        )
        assert registration.status_code == 201
        client_id = registration.json()["client_id"]
        verifier, challenge = _pkce()
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": "https://chatgpt.com/connector_platform_oauth_redirect",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": "https://gateway.sayori.org/mcp/fns",
            "scope": "notes:read files:read vaults:read",
        }
        login = client.get("/oauth/authorize", params=params)
        assert login.status_code == 200
        approved = client.post(
            "/oauth/authorize",
            data={**params, "username": "owner", "password": "login-secret"},
            follow_redirects=False,
        )
        assert approved.status_code == 302
        code = parse_qs(urlparse(approved.headers["location"]).query)["code"][0]
        exchanged = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": code,
                "redirect_uri": params["redirect_uri"],
                "code_verifier": verifier,
                "resource": params["resource"],
            },
        )
        assert exchanged.status_code == 200
        denied = client.get("/oauth/authorize", params={**params, "scope": "notes:admin"}, follow_redirects=False)
    finally:
        app.dependency_overrides.clear()

    assert exchanged.json()["scope"] == "mcp notes:read files:read vaults:read"
    assert denied.status_code == 302
    assert "invalid_scope" in denied.headers["location"]


def test_oauth_dcr_accepts_optional_refresh_token_metadata():
    app.dependency_overrides[get_settings] = _settings
    try:
        client = TestClient(app)
        registration = client.post(
            "/oauth/register",
            json={
                "client_name": "ChatGPT",
                "redirect_uris": ["https://chatgpt.com/connector_platform_oauth_redirect"],
                "token_endpoint_auth_method": "none",
                "response_types": ["code"],
                "grant_types": ["authorization_code", "refresh_token"],
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert registration.status_code == 201
    assert registration.json()["grant_types"] == ["authorization_code"]


def test_oauth_rejects_bad_pkce_and_unregistered_redirect():
    app.dependency_overrides[get_settings] = _settings
    try:
        client = TestClient(app)
        registration = client.post(
            "/oauth/register",
            json={"redirect_uris": ["https://chatgpt.com/connector/oauth/test"]},
        )
        client_id = registration.json()["client_id"]
        _, challenge = _pkce()
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": "https://chatgpt.com/connector/oauth/other",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": "https://gateway.sayori.org/mcp/search",
        }
        denied = client.get("/oauth/authorize", params=params, follow_redirects=False)
    finally:
        app.dependency_overrides.clear()

    assert denied.status_code == 400
    assert denied.json()["error"] == "invalid_request"


def test_signed_oauth_token_authorizes_mcp_and_missing_token_challenges():
    class Adapter:
        def handle(self, message):
            return {"jsonrpc": "2.0", "id": message.get("id"), "result": {}}

    mcp._SEARCH_ADAPTER = Adapter()
    token = issue_access_token(
        secret="signing-secret",
        issuer="https://gateway.sayori.org",
        resource="https://gateway.sayori.org/mcp/search",
        scope="mcp",
        subject="owner",
        ttl_seconds=3600,
    )
    app.dependency_overrides[get_settings] = _settings
    try:
        client = TestClient(app)
        denied = client.post("/mcp/search", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        allowed = client.post(
            "/mcp/search",
            headers={"Authorization": f"Bearer {token}"},
            json={"jsonrpc": "2.0", "id": 2, "method": "ping"},
        )
    finally:
        app.dependency_overrides.clear()

    assert denied.status_code == 401
    assert "resource_metadata=" in denied.headers["www-authenticate"]
    assert allowed.status_code == 200
    assert allowed.json()["id"] == 2
