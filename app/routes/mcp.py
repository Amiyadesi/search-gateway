"""Public Streamable HTTP MCP endpoints."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import Settings, get_settings
from app.mcp_security import (
    MCP_CONFIRMATIONS,
    MCP_RATE_LIMITER,
    McpAuthError,
    McpRateLimitError,
    token_fingerprint,
    validate_access_token,
)
from app.utils.errors import GatewayError
from app.utils.logging import logger


router = APIRouter(tags=["mcp"])
_SEARCH_ADAPTER: Any | None = None


def _search_adapter() -> Any:
    global _SEARCH_ADAPTER
    if _SEARCH_ADAPTER is None:
        from mcp import search_gateway_mcp

        _SEARCH_ADAPTER = search_gateway_mcp
    return _SEARCH_ADAPTER


def _client_identity(request: Request, token: str) -> str:
    address = request.client.host if request.client else "unknown"
    return f"{token_fingerprint(token)}:{address}"


def _authorize(request: Request, settings: Settings) -> str:
    try:
        token = validate_access_token(request.headers.get("Authorization"), settings.mcp_access_token)
        MCP_RATE_LIMITER.check(_client_identity(request, token), settings.mcp_rate_limit_per_minute)
        return token
    except McpAuthError as exc:
        raise GatewayError(str(exc), status_code=status.HTTP_401_UNAUTHORIZED) from exc
    except McpRateLimitError as exc:
        raise GatewayError(
            str(exc),
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"retryable": True},
        ) from exc


def _jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _is_destructive_tool(name: str) -> bool:
    lowered = name.lower().replace("-", "_")
    markers = (
        "delete",
        "remove",
        "destroy",
        "restore",
        "share_create",
        "share_delete",
        "user_delete",
        "setting_delete",
        "vault_delete",
        "folder_delete",
    )
    return any(marker in lowered for marker in markers)


def _confirmation_response(request_id: Any, tool_name: str, confirmation_id: str, ttl: int) -> dict[str, Any]:
    payload = {
        "code": "CONFIRMATION_REQUIRED",
        "tool": tool_name,
        "confirmation_id": confirmation_id,
        "expires_in_seconds": ttl,
        "message": "该操作可能造成数据或权限变化；确认后重复同一调用才会执行。",
    }
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "isError": True,
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        },
    }


async def _dispatch_search(message: dict[str, Any]) -> dict[str, Any] | None:
    return await asyncio.to_thread(_search_adapter().handle, message)


@router.post("/mcp/search")
async def search_mcp(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Response:
    _authorize(request, settings)
    try:
        message = await request.json()
    except Exception:
        return JSONResponse(_jsonrpc_error(None, -32700, "无效 JSON"), status_code=400)
    if not isinstance(message, dict):
        return JSONResponse(_jsonrpc_error(None, -32600, "MCP 请求必须是 JSON 对象"), status_code=400)
    response = await _dispatch_search(message)
    if response is None:
        return Response(status_code=status.HTTP_202_ACCEPTED)
    body = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
    if "text/event-stream" in request.headers.get("accept", ""):
        return StreamingResponse(
            _single_sse(body),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return JSONResponse(response)


async def _single_sse(body: str) -> AsyncIterator[str]:
    yield f"event: message\ndata: {body}\n\n"


@router.api_route(
    "/mcp/fns",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    name="fns_mcp_root",
    include_in_schema=False,
)
@router.api_route(
    "/mcp/fns/{suffix:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    name="fns_mcp_subpath",
    include_in_schema=False,
)
async def fns_mcp(
    request: Request,
    suffix: str = "",
    settings: Settings = Depends(get_settings),
) -> Response:
    token = _authorize(request, settings)
    if not settings.fns_mcp_token:
        raise GatewayError("FNS MCP 未配置内部 token", status_code=503)

    suffix = suffix.strip("/")
    upstream_path = {"": "/api/mcp", "sse": "/api/mcp/sse", "message": "/api/mcp/message"}.get(suffix)
    if upstream_path is None:
        raise GatewayError("未知 FNS MCP 路径", status_code=404)

    body = await request.body()
    forwarded_headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in {"host", "authorization", "content-length"}
    }
    if not any(key.lower() == "x-client" for key in forwarded_headers):
        # The gateway token is scoped to the dedicated AstrBot MCP client.
        forwarded_headers["X-Client"] = "AstrBot"
    forwarded_headers["Authorization"] = f"Bearer {settings.fns_mcp_token}"
    forwarded_headers["AccessToken"] = settings.fns_mcp_token

    if upstream_path == "/api/mcp" and body and "json" in request.headers.get("content-type", ""):
        try:
            message = json.loads(body)
        except json.JSONDecodeError:
            message = None
        if isinstance(message, dict) and message.get("method") == "tools/call":
            params = message.get("params") or {}
            tool_name = params.get("name") if isinstance(params, dict) else ""
            raw_arguments = params.get("arguments") if isinstance(params, dict) else {}
            arguments = dict(raw_arguments) if isinstance(raw_arguments, dict) else {}
            if isinstance(tool_name, str) and isinstance(raw_arguments, dict) and _is_destructive_tool(tool_name):
                confirmation_id = arguments.pop("__gateway_confirmation_id", None)
                if not isinstance(confirmation_id, str) or not MCP_CONFIRMATIONS.consume(confirmation_id, token, arguments):
                    confirmation_id, ttl = MCP_CONFIRMATIONS.issue(
                        token,
                        arguments,
                        settings.mcp_confirmation_ttl_seconds,
                    )
                    return JSONResponse(_confirmation_response(message.get("id"), tool_name, confirmation_id, ttl))
                message["params"]["arguments"] = arguments
                body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                forwarded_headers["content-type"] = "application/json"

    base_url = settings.fns_mcp_base_url.rstrip("/")
    upstream_url = f"{base_url}{upstream_path}"
    client = httpx.AsyncClient(timeout=settings.mcp_http_timeout_seconds, follow_redirects=False)
    try:
        upstream_request = client.build_request(
            request.method,
            upstream_url,
            params=request.query_params,
            headers=forwarded_headers,
            content=body or None,
        )
        upstream = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        logger.warning("FNS MCP upstream unavailable: {}", type(exc).__name__)
        raise GatewayError("FNS MCP 上游不可用", status_code=502, detail={"retryable": True}) from exc

    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower()
        in {
            "content-type",
            "cache-control",
            "x-accel-buffering",
            "mcp-session-id",
            "last-event-id",
            "www-authenticate",
            "location",
        }
    }

    async def stream() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(stream(), status_code=upstream.status_code, headers=response_headers)


__all__ = ["router"]
