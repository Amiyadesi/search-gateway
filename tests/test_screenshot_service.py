import asyncio

import pytest

from app.config import Settings
from app.schemas.screenshot import ScreenshotRequest
from app.services.screenshot_service import ScreenshotService
from app.utils.errors import GatewayError


class FakeCache:
    def __init__(self, *_args, **_kwargs):
        self.data = {}

    async def get_json(self, key):
        return self.data.get(key)

    async def set_json(self, key, value, ttl=None):
        self.data[key] = value

    async def close(self):
        return None


class FakeResponse:
    def __init__(self, content=b"", content_type="image/png", data=None):
        self.content = content
        self.headers = {"content-type": content_type}
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data or {}


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.response

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.response


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize(
    ("provider", "settings_kwargs", "expected_method", "expected_url_part"),
    [
        ("apiflash", {"apiflash_access_key": "token"}, "GET", "urltoimage"),
        ("microlink", {}, "GET", "microlink"),
        ("screenshotlayer", {"screenshotlayer_access_key": "token"}, "GET", "screenshotlayer"),
        ("screenshotmachine", {"screenshotmachine_key": "token"}, "GET", "screenshotmachine"),
        ("phantomjscloud", {"phantomjscloud_api_key": "token"}, "POST", "phantomjscloud"),
        ("snapapi", {"snapapi_api_key": "token"}, "POST", "snapapi"),
        ("screenshotbase", {"screenshotbase_api_key": "token"}, "GET", "screenshotbase"),
        (
            "screenshotscout",
            {"screenshotscout_access_key": "token", "screenshotscout_secret_key": "secret"},
            "POST",
            "screenshotscout",
        ),
        ("thumbnailws", {"thumbnail_ws_api_key": "token"}, "GET", "thumbnail"),
        ("hqapi", {"hqapi_screenshot_key": "token"}, "GET", "hqapi"),
    ],
)
def test_screenshot_provider_request_shapes(monkeypatch, provider, settings_kwargs, expected_method, expected_url_part):
    service = ScreenshotService(Settings(gateway_api_key="test", **settings_kwargs))
    client = FakeClient(FakeResponse(content=b"image"))

    # Test request construction directly to avoid provider-specific live calls.
    response = run(service._request_provider(client, provider, ScreenshotRequest(url="https://example.com")))

    assert response is client.response
    assert client.calls[0][0] == expected_method
    assert expected_url_part in client.calls[0][1]


def test_screenshot_capture_caches_image(monkeypatch):
    service = ScreenshotService(
        Settings(
            gateway_api_key="test",
            apiflash_access_key="token",
            screenshot_provider_order="apiflash",
            screenshot_allow_private_urls=True,
        )
    )
    fake_cache = FakeCache()
    service.cache = fake_cache

    async def fake_capture(provider, request):
        from app.services.screenshot_service import ScreenshotCapture

        return ScreenshotCapture(provider=provider, content=b"image", content_type="image/png")

    monkeypatch.setattr(service, "_capture_with_provider", fake_capture)

    result = run(service.capture(ScreenshotRequest(url="https://example.com")))
    cached = run(service.capture(ScreenshotRequest(url="https://example.com")))

    assert result.provider == "apiflash"
    assert result.image_url and result.image_url.startswith("/screenshot-cache/screenshot:")
    assert cached.cached is True
    assert cached.cache_id == result.cache_id


def test_screenshot_rejects_private_url_without_flag():
    service = ScreenshotService(Settings(gateway_api_key="test", apiflash_access_key="token"))

    with pytest.raises(GatewayError):
        run(service.capture(ScreenshotRequest(url="http://127.0.0.1:8000")))


def test_screenshot_returns_degraded_when_all_providers_fail(monkeypatch):
    service = ScreenshotService(
        Settings(
            gateway_api_key="test",
            apiflash_access_key="token",
            screenshot_provider_order="apiflash",
            screenshot_allow_private_urls=True,
        )
    )
    service.cache = FakeCache()

    async def fake_capture(provider, request):
        raise GatewayError("boom", status_code=502)

    monkeypatch.setattr(service, "_capture_with_provider", fake_capture)

    result = run(service.capture(ScreenshotRequest(url="https://example.com")))

    assert result.degraded is True
    assert result.error == "boom"
