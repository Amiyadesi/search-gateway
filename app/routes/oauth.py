"""OAuth 2.1 authorization endpoints used by the public MCP transports."""

from __future__ import annotations

import html
import secrets
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app.config import Settings, get_settings
from app.oauth import (
    AUTHORIZATION_CODES,
    OAuthError,
    issue_access_token,
    issue_authorization_code,
    issue_client_id,
    read_client_id,
    validate_pkce_challenge,
    validate_redirect_uri,
    validate_resource,
    verify_pkce,
)


router = APIRouter(tags=["oauth"])
OAUTH_SCOPE = "mcp"
# FNS advertises these per-tool scopes in tools/list. Keep the gateway scope
# for the Search MCP and legacy clients, while allowing ChatGPT to request
# the granular FNS permissions during OAuth.
FNS_SCOPES = ("notes:read", "notes:write", "files:read", "files:write", "vaults:read")
SUPPORTED_SCOPES = (OAUTH_SCOPE, *FNS_SCOPES)


def _issuer(settings: Settings) -> str:
    return settings.mcp_oauth_issuer.rstrip("/")


def _signing_secret(settings: Settings) -> str:
    return settings.mcp_oauth_signing_secret or settings.mcp_access_token


def _login_secret(settings: Settings) -> str:
    return settings.mcp_oauth_login_secret or settings.mcp_access_token


def _ready(settings: Settings) -> bool:
    return bool(_issuer(settings) and _signing_secret(settings) and _login_secret(settings))


def _json_error(exc: OAuthError) -> JSONResponse:
    response = JSONResponse(
        {"error": exc.code, "error_description": exc.description},
        status_code=exc.status_code,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )
    if exc.status_code == status.HTTP_401_UNAUTHORIZED:
        response.headers["WWW-Authenticate"] = 'Basic realm="oauth"'
    return response


def _oauth_error_redirect(redirect_uri: str, issuer: str, error: str, description: str, state: str) -> Response:
    parsed = urlparse(redirect_uri)
    query = dict(parse_qs(parsed.query, keep_blank_values=True))
    query.update(
        {
            "error": [error],
            "error_description": [description],
            "iss": [issuer],
        }
    )
    if state:
        query["state"] = [state]
    location = parsed._replace(query=urlencode(query, doseq=True)).geturl()
    return RedirectResponse(location, status_code=status.HTTP_302_FOUND, headers={"Cache-Control": "no-store"})


def _oauth_success_redirect(redirect_uri: str, issuer: str, code: str, state: str) -> Response:
    parsed = urlparse(redirect_uri)
    query = dict(parse_qs(parsed.query, keep_blank_values=True))
    query.update({"code": [code], "iss": [issuer]})
    if state:
        query["state"] = [state]
    location = parsed._replace(query=urlencode(query, doseq=True)).geturl()
    return RedirectResponse(location, status_code=status.HTTP_302_FOUND, headers={"Cache-Control": "no-store"})


def _form_values(body: bytes) -> dict[str, str]:
    parsed = parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items() if values}


def _normalize_scopes(raw: str | None) -> str:
    requested = set((raw or OAUTH_SCOPE).split())
    requested.discard("")
    if not requested:
        requested = {OAUTH_SCOPE}
    unsupported = requested - set(SUPPORTED_SCOPES)
    if unsupported:
        raise OAuthError("invalid_scope", "请求了不支持的 scope")
    # Always retain the gateway scope so existing MCP middleware and legacy
    # clients continue to work when a client asks only for FNS granular scopes.
    return " ".join(scope for scope in SUPPORTED_SCOPES if scope == OAUTH_SCOPE or scope in requested)


def _login_form(params: Mapping[str, str], *, error: str = "") -> str:
    fields = "\n".join(
        f'<input type="hidden" name="{html.escape(key, quote=True)}" value="{html.escape(value, quote=True)}">'
        for key, value in params.items()
        if key not in {"username", "password"}
    )
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sayori MCP 授权</title>
<style>body{{font:16px system-ui,sans-serif;max-width:28rem;margin:4rem auto;padding:0 1rem}}label{{display:block;margin:.8rem 0 .25rem}}input{{box-sizing:border-box;width:100%;padding:.6rem}}button{{margin-top:1rem;padding:.6rem 1rem}}.error{{color:#b00020}}</style>
</head><body><h1>Sayori MCP 授权</h1><p>登录后允许此 MCP 客户端访问已授权的网关工具。</p>{error_html}
<form method="post" action="/oauth/authorize">{fields}
<label for="username">用户名</label><input id="username" name="username" autocomplete="username" required>
<label for="password">授权口令</label><input id="password" name="password" type="password" autocomplete="current-password" required>
<button type="submit">允许访问</button></form></body></html>"""


def _authorize_params(request: Request) -> dict[str, str]:
    return {key: value for key, value in request.query_params.items()}


def _validate_authorize(params: Mapping[str, str], settings: Settings) -> tuple[str, dict[str, Any], str, str, str, str]:
    if params.get("response_type") != "code":
        raise OAuthError("unsupported_response_type", "仅支持 authorization code")
    client_id = params.get("client_id", "")
    client = read_client_id(client_id, _signing_secret(settings))
    redirect_uri = validate_redirect_uri(params.get("redirect_uri"))
    if redirect_uri not in client.get("redirect_uris", []):
        raise OAuthError("invalid_request", "redirect_uri 未注册")
    challenge = validate_pkce_challenge(params.get("code_challenge"), params.get("code_challenge_method"))
    resource = validate_resource(params.get("resource"), _issuer(settings))
    return client_id, client, redirect_uri, challenge, resource, _normalize_scopes(params.get("scope"))


@router.get("/.well-known/oauth-protected-resource", include_in_schema=False)
async def protected_resource(settings: Settings = Depends(get_settings)) -> JSONResponse:
    issuer = _issuer(settings)
    return JSONResponse(
        {
            "resource": issuer,
            "authorization_servers": [issuer],
            "scopes_supported": list(SUPPORTED_SCOPES),
            "resource_documentation": f"{issuer}/docs",
        },
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get("/.well-known/oauth-authorization-server", include_in_schema=False)
async def authorization_server_metadata(settings: Settings = Depends(get_settings)) -> JSONResponse:
    issuer = _issuer(settings)
    return JSONResponse(
        {
            "issuer": issuer,
            "authorization_response_iss_parameter_supported": True,
            "authorization_endpoint": f"{issuer}/oauth/authorize",
            "token_endpoint": f"{issuer}/oauth/token",
            "registration_endpoint": f"{issuer}/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "token_endpoint_auth_methods_supported": ["none"],
            "code_challenge_methods_supported": ["S256"],
            "scopes_supported": list(SUPPORTED_SCOPES),
            "client_id_metadata_document_supported": False,
        },
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.post("/oauth/register", include_in_schema=False)
async def register_client(request: Request, settings: Settings = Depends(get_settings)) -> Response:
    if not _ready(settings):
        return JSONResponse({"error": "temporarily_unavailable"}, status_code=503)
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise OAuthError("invalid_client_metadata", "注册请求必须是 JSON 对象")
        redirect_uris = payload.get("redirect_uris")
        if not isinstance(redirect_uris, list) or not redirect_uris or len(redirect_uris) > 8:
            raise OAuthError("invalid_client_metadata", "必须提供 redirect_uris")
        normalized = []
        for value in redirect_uris:
            uri = validate_redirect_uri(value)
            if uri not in normalized:
                normalized.append(uri)
        auth_method = payload.get("token_endpoint_auth_method", "none")
        if auth_method != "none":
            raise OAuthError("invalid_client_metadata", "仅支持 public client（token_endpoint_auth_method=none）")
        response_types = payload.get("response_types", ["code"])
        if (
            not isinstance(response_types, list)
            or not response_types
            or any(not isinstance(value, str) for value in response_types)
            or set(response_types) != {"code"}
        ):
            raise OAuthError("invalid_client_metadata", "仅支持 response_type=code")
        grant_types = payload.get("grant_types", ["authorization_code"])
        if (
            not isinstance(grant_types, list)
            or not grant_types
            or any(not isinstance(value, str) for value in grant_types)
            or "authorization_code" not in grant_types
            or set(grant_types) - {"authorization_code", "refresh_token"}
        ):
            raise OAuthError("invalid_client_metadata", "仅支持 authorization_code（可声明 refresh_token）")
        client_name = payload.get("client_name", "MCP client")
        if not isinstance(client_name, str):
            client_name = "MCP client"
        client_id = issue_client_id(
            normalized,
            _signing_secret(settings),
            client_name=client_name,
            ttl_seconds=settings.mcp_oauth_client_ttl_seconds,
        )
        return JSONResponse(
            {
                "client_id": client_id,
                "client_id_issued_at": int(time.time()),
                "client_id_expires_at": int(time.time()) + settings.mcp_oauth_client_ttl_seconds,
                "redirect_uris": normalized,
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
            },
            status_code=status.HTTP_201_CREATED,
            headers={"Cache-Control": "no-store"},
        )
    except OAuthError as exc:
        return _json_error(exc)
    except Exception:
        return _json_error(OAuthError("invalid_client_metadata", "注册请求无效"))


@router.api_route("/oauth/authorize", methods=["GET", "POST"], include_in_schema=False)
async def authorize(request: Request, settings: Settings = Depends(get_settings)) -> Response:
    if not _ready(settings):
        return JSONResponse({"error": "temporarily_unavailable"}, status_code=503)
    params = _authorize_params(request) if request.method == "GET" else _form_values(await request.body())
    state = params.get("state", "")
    try:
        client_id, _, redirect_uri, challenge, resource, scope = _validate_authorize(params, settings)
    except OAuthError as exc:
        redirect_uri = params.get("redirect_uri", "")
        if redirect_uri:
            try:
                validate_redirect_uri(redirect_uri)
                client = read_client_id(params.get("client_id", ""), _signing_secret(settings))
                if redirect_uri not in client.get("redirect_uris", []):
                    redirect_uri = ""
            except OAuthError:
                redirect_uri = ""
        if redirect_uri:
            return _oauth_error_redirect(redirect_uri, _issuer(settings), exc.code, exc.description, state)
        return _json_error(exc)

    if request.method == "GET":
        return HTMLResponse(
            _login_form(params),
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
            },
        )

    username = params.get("username", "")
    password = params.get("password", "")
    expected_user = settings.mcp_oauth_login_username
    if not secrets.compare_digest(username, expected_user) or not secrets.compare_digest(password, _login_secret(settings)):
        return HTMLResponse(
            _login_form(params, error="用户名或授权口令不正确"),
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"},
        )

    code = issue_authorization_code(
        AUTHORIZATION_CODES,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=challenge,
        resource=resource,
        scope=scope,
        subject=expected_user,
        ttl_seconds=settings.mcp_oauth_code_ttl_seconds,
    )
    return _oauth_success_redirect(redirect_uri, _issuer(settings), code, state)


@router.post("/oauth/token", include_in_schema=False)
async def token(request: Request, settings: Settings = Depends(get_settings)) -> Response:
    if not _ready(settings):
        return JSONResponse({"error": "temporarily_unavailable"}, status_code=503)
    params = _form_values(await request.body())
    try:
        if request.headers.get("Authorization"):
            raise OAuthError("invalid_client", "此 token endpoint 仅接受 public client", status_code=401)
        if params.get("grant_type") != "authorization_code":
            raise OAuthError("unsupported_grant_type", "仅支持 authorization_code")
        client_id = params.get("client_id", "")
        read_client_id(client_id, _signing_secret(settings))
        code = params.get("code", "")
        if not code:
            raise OAuthError("invalid_request", "缺少 code")
        authorization = AUTHORIZATION_CODES.pop(code)
        if authorization is None:
            raise OAuthError("invalid_grant", "authorization code 无效或已使用")
        if authorization.client_id != client_id:
            raise OAuthError("invalid_grant", "authorization code 不属于此 client")
        if params.get("redirect_uri") != authorization.redirect_uri:
            raise OAuthError("invalid_grant", "redirect_uri 不匹配")
        resource = validate_resource(params.get("resource"), _issuer(settings))
        if resource != authorization.resource:
            raise OAuthError("invalid_grant", "resource 不匹配")
        if not verify_pkce(params.get("code_verifier"), authorization.code_challenge):
            raise OAuthError("invalid_grant", "PKCE 校验失败")
        access_token = issue_access_token(
            secret=_signing_secret(settings),
            issuer=_issuer(settings),
            resource=authorization.resource,
            scope=authorization.scope,
            subject=authorization.subject,
            ttl_seconds=settings.mcp_oauth_access_token_ttl_seconds,
        )
        return JSONResponse(
            {
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": settings.mcp_oauth_access_token_ttl_seconds,
                "scope": authorization.scope,
                "resource": authorization.resource,
            },
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    except OAuthError as exc:
        return _json_error(exc)


__all__ = ["router"]
