from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator


SCREENSHOT_PROVIDERS = [
    "auto",
    "snapapi",
    "apiflash",
    "microlink",
    "screenshotlayer",
    "phantomjscloud",
    "screenshotbase",
    "screenshotscout",
    "screenshotmachine",
    "thumbnailws",
    "hqapi",
]
SCREENSHOT_PROVIDER_PATTERN = "^(" + "|".join(SCREENSHOT_PROVIDERS) + ")$"
SCREENSHOT_MODES = ["auto", "never", "force"]
SCREENSHOT_MODE_PATTERN = "^(" + "|".join(SCREENSHOT_MODES) + ")$"


class ScreenshotRequest(BaseModel):
    url: HttpUrl
    provider: str = Field(default="auto")
    width: int = Field(default=1280, ge=320, le=3840)
    height: int = Field(default=720, ge=240, le=2160)
    full_page: bool = False
    format: Literal["png", "jpg", "jpeg", "webp"] = "png"
    wait_until: Literal["page_loaded", "network_idle", "dom_loaded"] = "page_loaded"
    delay_ms: int = Field(default=0, ge=0, le=10000)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        if value not in SCREENSHOT_PROVIDERS:
            raise ValueError(f"unsupported screenshot provider: {value}")
        return value


class ScreenshotMetadata(BaseModel):
    provider: str
    cache_id: str | None = None
    image_url: str | None = None
    content_type: str | None = None
    width: int | None = None
    height: int | None = None
    cached: bool = False
    degraded: bool = False
    error: str | None = None


class ScreenshotResponse(BaseModel):
    success: bool
    screenshot: ScreenshotMetadata


class ScreenshotCacheEntry(BaseModel):
    content_base64: str
    content_type: str
    provider: str
    width: int
    height: int
