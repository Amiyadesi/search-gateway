import secrets

from fastapi import Depends, Header

from app.config import Settings, get_settings
from app.utils.errors import GatewayError


async def require_api_key(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    """校验自用网关密钥，支持 Bearer 和 X-API-Key 两种方式。"""
    expected = settings.gateway_api_key
    if not expected:
        raise GatewayError("服务未配置 GATEWAY_API_KEY", status_code=500)

    provided = x_api_key or ""
    if authorization and authorization.lower().startswith("bearer "):
        provided = authorization.split(" ", 1)[1].strip()

    if not provided or not secrets.compare_digest(provided, expected):
        raise GatewayError("未授权访问", status_code=401)
