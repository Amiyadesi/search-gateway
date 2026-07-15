from typing import Any

from pydantic import BaseModel


class IpInfoResponse(BaseModel):
    success: bool
    provider: str
    ip: str
    cached: bool
    data: dict[str, Any]
