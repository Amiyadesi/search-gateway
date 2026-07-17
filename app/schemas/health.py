from typing import Literal

from pydantic import BaseModel


class ProviderHealth(BaseModel):
    configured: bool
    model: str | None = None
    upstreams: int | None = None


class HealthResponse(BaseModel):
    success: bool
    api: str
    redis: str
    providers: dict[str, ProviderHealth]


class DependencyHealth(BaseModel):
    status: Literal["ok", "disabled", "misconfigured", "unavailable"]
    required: bool
    configured: bool


class ReadinessResponse(BaseModel):
    success: bool
    status: Literal["ready", "not_ready"]
    checks: dict[str, DependencyHealth]
