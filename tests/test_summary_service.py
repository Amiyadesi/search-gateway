import asyncio

import httpx

from app.config import Settings
from app.services.summary_service import SummaryService
from app.utils.errors import GatewayError


def test_summary_context_truncation():
    service = SummaryService(Settings(gateway_api_key="test", summary_context_max_chars=10))
    assert service._truncate_context("123456789012345").startswith("1234567890")
    assert "上下文已截断" in service._truncate_context("123456789012345")


def test_summary_model_retries_after_transient_network_error(monkeypatch):
    service = SummaryService(
        Settings(
            gateway_api_key="test",
            summary_provider="custom",
            summary_base_url="https://example.com/v1",
            summary_api_key="token",
            summary_retry_attempts=2,
        )
    )

    calls = {"count": 0, "headers": {}}

    class FakeResponse:
        def raise_for_status(self) -> None:
            if calls["count"] == 1:
                raise httpx.ConnectError("boom", request=httpx.Request("POST", "https://example.com/v1/chat/completions"))

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "ok"}}]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            calls["count"] += 1
            calls["headers"] = kwargs.get("headers") or {}
            return FakeResponse()

    def fake_build_client(*args, **kwargs):
        return FakeClient()

    monkeypatch.setattr("app.services.summary_service.build_client", fake_build_client)
    result = asyncio.run(service._call_summary_model("q", "ctx", []))

    assert result == "ok"
    assert calls["count"] == 2
    assert calls["headers"]["User-Agent"] == "Mozilla/5.0"
    assert calls["headers"]["Accept"] == "application/json, text/plain, */*"


def test_summary_fallback_when_model_times_out(monkeypatch):
    service = SummaryService(
        Settings(
            gateway_api_key="test",
            summary_provider="custom",
            summary_base_url="https://example.com/v1",
            summary_api_key="token",
            summary_fallback_enabled=True,
            screenshot_min_markdown_chars=1,
        )
    )

    async def fake_search(*args, **kwargs):
        from app.schemas.common import SearchResult
        from app.schemas.search import SearchResponse

        return SearchResponse(
            success=True,
            provider="brave",
            query="q",
            cached=False,
            results=[
                SearchResult(title="A", url="https://example.com/a", snippet="snippet a"),
                SearchResult(title="B", url="https://example.com/b", snippet="snippet b"),
            ],
        )

    monkeypatch.setattr(service.router, "search", fake_search)

    monkeypatch.setattr(service.extractor, "extract", lambda *_args, **_kwargs: asyncio.sleep(0, "markdown"))

    async def fake_summary_failure(*args, **kwargs):
        raise GatewayError("SummaryModel 调用超时", status_code=504)

    monkeypatch.setattr(service, "_call_summary_model", fake_summary_failure)

    async def run():
        return await service.summarize("q")

    result = asyncio.run(run())
    assert result.success is True
    assert result.degraded is True
    assert "降级摘要" in result.summary
    assert result.error


def test_analyze_url_returns_markdown_when_model_falls_back(monkeypatch):
    service = SummaryService(
        Settings(
            gateway_api_key="test",
            summary_provider="custom",
            summary_base_url="https://example.com/v1",
            summary_api_key="token",
            summary_fallback_enabled=True,
        )
    )

    async def fake_extract(url):
        return "# Page\n\nImportant content."

    async def fake_summary_failure(*args, **kwargs):
        raise GatewayError("SummaryModel 调用超时", status_code=504)

    monkeypatch.setattr(service.extractor, "extract", fake_extract)
    monkeypatch.setattr(service, "_call_summary_model", fake_summary_failure)

    result = asyncio.run(service.analyze_url("https://example.com", "总结页面"))

    assert result.success is True
    assert result.degraded is True
    assert result.markdown == "# Page\n\nImportant content."
    assert "降级摘要" in result.analysis


def test_research_can_hide_markdown_contexts_by_default(monkeypatch):
    service = SummaryService(
        Settings(
            gateway_api_key="test",
            summary_provider="custom",
            summary_base_url="https://example.com/v1",
            summary_api_key="token",
            screenshot_min_markdown_chars=1,
        )
    )

    async def fake_search(*args, **kwargs):
        from app.schemas.common import SearchResult
        from app.schemas.search import SearchResponse

        return SearchResponse(
            success=True,
            provider="brave",
            query="q",
            cached=False,
            results=[SearchResult(title="A", url="https://example.com/a", snippet="snippet a")],
        )

    async def fake_extract(url):
        return "full markdown"

    async def fake_summary(*args, **kwargs):
        return "summary"

    monkeypatch.setattr(service.router, "search", fake_search)
    monkeypatch.setattr(service.extractor, "extract", fake_extract)
    monkeypatch.setattr(service, "_call_summary_model", fake_summary)

    hidden = asyncio.run(service.research("q", include_markdown=False))
    included = asyncio.run(service.research("q", include_markdown=True))

    assert hidden.success is True
    assert hidden.provider == "brave"
    assert hidden.contexts[0].markdown == ""
    assert hidden.contexts[0].extracted is True
    assert included.contexts[0].markdown == "full markdown"


def test_analyze_url_auto_screenshot_when_extract_fails(monkeypatch):
    from app.schemas.screenshot import ScreenshotMetadata

    service = SummaryService(
        Settings(
            gateway_api_key="test",
            summary_provider="custom",
            summary_base_url="https://example.com/v1",
            summary_api_key="token",
            summary_fallback_enabled=True,
            screenshot_allow_private_urls=True,
        )
    )

    async def fake_extract(url):
        raise GatewayError("Firecrawl 调用失败", status_code=502)

    async def fake_capture(request):
        return ScreenshotMetadata(
            provider="apiflash",
            cache_id="screenshot:abc",
            image_url="/screenshot-cache/screenshot:abc",
            content_type="image/png",
        )

    async def fake_summary(*args, **kwargs):
        return "analysis"

    monkeypatch.setattr(service.extractor, "extract", fake_extract)
    monkeypatch.setattr(service.screenshots, "capture", fake_capture)
    monkeypatch.setattr(service, "_call_summary_model", fake_summary)

    result = asyncio.run(service.analyze_url("https://example.com", "总结页面"))

    assert result.success is True
    assert result.degraded is True
    assert result.screenshot is not None
    assert result.screenshot.provider == "apiflash"
    assert "截图兜底" in result.markdown


def test_research_never_screenshot_when_mode_never(monkeypatch):
    service = SummaryService(
        Settings(
            gateway_api_key="test",
            summary_provider="custom",
            summary_base_url="https://example.com/v1",
            summary_api_key="token",
        )
    )

    async def fake_search(*args, **kwargs):
        from app.schemas.common import SearchResult
        from app.schemas.search import SearchResponse

        return SearchResponse(
            success=True,
            provider="brave",
            query="q",
            cached=False,
            results=[SearchResult(title="A", url="https://example.com/a", snippet="snippet a")],
        )

    async def fake_extract(url):
        raise GatewayError("Firecrawl 调用失败", status_code=502)

    async def fail_capture(*args, **kwargs):
        raise AssertionError("screenshot should not be called")

    async def fake_summary(*args, **kwargs):
        return "summary"

    monkeypatch.setattr(service.router, "search", fake_search)
    monkeypatch.setattr(service.extractor, "extract", fake_extract)
    monkeypatch.setattr(service.screenshots, "capture", fail_capture)
    monkeypatch.setattr(service, "_call_summary_model", fake_summary)

    result = asyncio.run(service.research("q", screenshot_mode="never", include_markdown=True))

    assert result.success is True
    assert result.screenshots == []
    assert result.contexts[0].markdown == "snippet a"
