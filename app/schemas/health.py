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
