from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, Query

from app.config import Settings, get_settings
from app.utils.auth import require_api_key
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call

router = APIRouter(tags=["bing-webmaster"])

API_BASE = "https://www.bing.com/webmaster/api.svc/json/"
METHOD_FIELDS = {
    "GetUserSites": ("Url", "IsVerified"),
    "GetRankAndTrafficStats": ("Date", "Clicks", "Impressions"),
    "GetQueryStats": (
        "Query",
        "Date",
        "Clicks",
        "Impressions",
        "AvgClickPosition",
        "AvgImpressionPosition",
    ),
}


@router.get("/bing-webmaster/{method}")
async def bing_webmaster(
    method: Literal["GetUserSites", "GetRankAndTrafficStats", "GetQueryStats"],
    site_url: str = Query(default="", max_length=2048),
    _: None = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
) -> dict[str, list[dict[str, Any]]]:
    key = settings.bing_webmaster_api_key.strip()
    if not key:
        raise GatewayError("Bing Webmaster API 未配置", status_code=503)
    if method != "GetUserSites" and not site_url.startswith(("http://", "https://")):
        raise GatewayError("Bing Webmaster 站点 URL 无效", status_code=400)

    params = {"apikey": key}
    if site_url:
        params["siteUrl"] = site_url
    async with build_client(settings, settings.bing_webmaster_timeout_seconds) as client:
        response = await timed_call(
            "Bing Webmaster",
            lambda: _request(client, method, params),
        )

    try:
        rows = response.json().get("d")
    except (TypeError, ValueError) as exc:
        raise GatewayError("Bing Webmaster 返回无效 JSON", status_code=502) from exc
    if not isinstance(rows, list):
        raise GatewayError("Bing Webmaster 返回无效数据", status_code=502)

    fields = METHOD_FIELDS[method]
    return {
        "d": [
            {field: row.get(field) for field in fields}
            for row in rows
            if isinstance(row, dict)
        ][:400]
    }


async def _request(
    client: httpx.AsyncClient,
    method: str,
    params: dict[str, str],
) -> httpx.Response:
    response = await client.get(API_BASE + method, params=params, headers={"Accept": "application/json"})
    response.raise_for_status()
    return response
