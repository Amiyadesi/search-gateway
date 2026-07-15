from pydantic import BaseModel, Field, HttpUrl, field_validator

from app.schemas.screenshot import SCREENSHOT_MODES, ScreenshotMetadata


class ExtractRequest(BaseModel):
    url: HttpUrl
    screenshot_mode: str = Field(default="auto")

    @field_validator("screenshot_mode")
    @classmethod
    def validate_screenshot_mode(cls, value: str) -> str:
        if value not in SCREENSHOT_MODES:
            raise ValueError(f"unsupported screenshot mode: {value}")
        return value


class ExtractResponse(BaseModel):
    success: bool
    markdown: str
    screenshot: ScreenshotMetadata | None = None
    degraded: bool = False
    error: str | None = None
