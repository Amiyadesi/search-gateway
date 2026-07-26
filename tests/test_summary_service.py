import asyncio
import time

import httpx
import pytest

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


def test_research_accepts_wrapped_openai_response(monkeypatch):
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

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"data": {"choices": [{"message": {"content": "wrapped summary"}}]}}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(service.router, "search", fake_search)
    monkeypatch.setattr(service.extractor, "extract", fake_extract)
    monkeypatch.setattr("app.services.summary_service.build_client", lambda *args, **kwargs: FakeClient())

    result = asyncio.run(service.research("q", screenshot_mode="never"))

    assert result.success is True
    assert result.summary == "wrapped summary"


def test_research_degrades_when_summary_response_is_invalid(monkeypatch):
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
            results=[SearchResult(title="A", url="https://example.com/a", snippet="snippet a")],
        )

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"data": {"private_upstream_error": "do not expose"}}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(service.router, "search", fake_search)
    monkeypatch.setattr(service.extractor, "extract", lambda *_: asyncio.sleep(0, "full markdown"))
    monkeypatch.setattr("app.services.summary_service.build_client", lambda *args, **kwargs: FakeClient())

    result = asyncio.run(service.research("q", screenshot_mode="never"))

    assert result.success is True
    assert result.degraded is True
    assert result.error == "SummaryModel 返回格式无效"
    assert "do not expose" not in result.summary


def test_research_extracts_sources_concurrently(monkeypatch):
    service = SummaryService(
        Settings(
            gateway_api_key="test",
            summary_provider="custom",
            summary_base_url="https://example.com/v1",
            summary_api_key="token",
            evidence_extract_concurrency=3,
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
                SearchResult(title=str(index), url=f"https://example{index}.com", snippet=f"snippet {index}")
                for index in range(3)
            ],
        )

    async def slow_extract(url):
        await asyncio.sleep(0.08)
        return f"markdown for {url}"

    monkeypatch.setattr(service.router, "search", fake_search)
    monkeypatch.setattr(service.extractor, "extract", slow_extract)
    monkeypatch.setattr(service, "_call_summary_model", lambda *args, **kwargs: asyncio.sleep(0, "summary"))

    started = time.perf_counter()
    result = asyncio.run(service.research("q", screenshot_mode="never"))
    elapsed = time.perf_counter() - started

    assert result.success is True
    assert len(result.contexts) == 3
    assert elapsed < 0.16


def test_research_returns_partial_contexts_when_one_extract_times_out(monkeypatch):
    service = SummaryService(
        Settings(
            gateway_api_key="test",
            summary_provider="custom",
            summary_base_url="https://example.com/v1",
            summary_api_key="token",
            request_timeout_seconds=0.03,
            evidence_extract_concurrency=2,
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
                SearchResult(title="Fast", url="https://fast.example", snippet="fast snippet"),
                SearchResult(title="Slow", url="https://slow.example", snippet="slow snippet"),
            ],
        )

    async def variable_extract(url):
        if "slow" in url:
            await asyncio.sleep(0.2)
        return f"markdown for {url}"

    monkeypatch.setattr(service.router, "search", fake_search)
    monkeypatch.setattr(service.extractor, "extract", variable_extract)
    monkeypatch.setattr(service, "_call_summary_model", lambda *args, **kwargs: asyncio.sleep(0, "summary"))

    started = time.perf_counter()
    result = asyncio.run(service.research("q", screenshot_mode="never", include_markdown=True))
    elapsed = time.perf_counter() - started

    assert elapsed < 0.12
    assert result.success is True
    assert result.partial is True
    assert result.degraded is True
    assert [item.status for item in result.contexts] == ["complete", "timeout"]
    assert result.contexts[1].markdown == "slow snippet"
    assert result.contexts[1].error == "页面正文提取超时"


def test_research_auto_mode_skips_screenshot_when_search_snippet_is_usable(monkeypatch):
    service = SummaryService(
        Settings(
            gateway_api_key="test",
            summary_provider="custom",
            summary_base_url="https://example.com/v1",
            summary_api_key="token",
            screenshot_min_markdown_chars=300,
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
                SearchResult(
                    title="A",
                    url="https://example.com/a",
                    snippet=(
                        "This search result already contains enough readable context for a degraded summary. "
                        "It preserves the main claim, supporting details, and a source description without "
                        "requiring an expensive screenshot fallback."
                    ),
                )
            ],
        )

    async def fail_extract(url):
        raise GatewayError("extract failed", status_code=502)

    async def fail_capture(*args, **kwargs):
        raise AssertionError("research should not capture a screenshot when readable text exists")

    monkeypatch.setattr(service.router, "search", fake_search)
    monkeypatch.setattr(service.extractor, "extract", fail_extract)
    monkeypatch.setattr(service.screenshots, "capture", fail_capture)
    monkeypatch.setattr(service, "_call_summary_model", lambda *args, **kwargs: asyncio.sleep(0, "summary"))

    result = asyncio.run(service.research("q", screenshot_mode="auto", include_markdown=True))

    assert result.success is True
    assert result.partial is True
    assert result.contexts[0].status == "error"
    assert result.contexts[0].screenshot is None


def test_research_falls_back_within_total_summary_timeout(monkeypatch):
    service = SummaryService(
        Settings(
            gateway_api_key="test",
            summary_provider="custom",
            summary_base_url="https://example.com/v1",
            summary_api_key="token",
            summary_timeout_seconds=0.03,
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
            results=[SearchResult(title="A", url="https://example.com/a", snippet="snippet")],
        )

    class SlowClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            await asyncio.sleep(0.2)
            raise AssertionError("summary timeout was not enforced")

    monkeypatch.setattr(service.router, "search", fake_search)
    monkeypatch.setattr(service.extractor, "extract", lambda *_: asyncio.sleep(0, "full markdown"))
    monkeypatch.setattr("app.services.summary_service.build_client", lambda *args, **kwargs: SlowClient())

    started = time.perf_counter()
    result = asyncio.run(service.research("q", screenshot_mode="never"))
    elapsed = time.perf_counter() - started

    assert elapsed < 0.12
    assert result.success is True
    assert result.partial is False
    assert result.degraded is True
    assert result.error == "SummaryModel 调用超时"


def test_research_search_phase_respects_overall_deadline(monkeypatch):
    service = SummaryService(
        Settings(
            gateway_api_key="test",
            research_timeout_seconds=0.03,
        )
    )

    async def slow_search(*args, **kwargs):
        await asyncio.sleep(0.2)
        raise AssertionError("research deadline was not enforced")

    monkeypatch.setattr(service.router, "search", slow_search)

    started = time.perf_counter()
    with pytest.raises(GatewayError) as exc_info:
        asyncio.run(service.research("q", screenshot_mode="never"))
    elapsed = time.perf_counter() - started

    assert elapsed < 0.1
    assert exc_info.value.status_code == 504
    assert exc_info.value.detail == {
        "code": "RESEARCH_TIMEOUT",
        "retryable": True,
        "phase": "search",
    }


def test_research_deadline_returns_partial_without_calling_model(monkeypatch):
    service = SummaryService(
        Settings(
            gateway_api_key="test",
            research_timeout_seconds=0.04,
            request_timeout_seconds=0.2,
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
            results=[SearchResult(title="A", url="https://slow.example", snippet="usable search snippet")],
        )

    async def slow_extract(url):
        await asyncio.sleep(0.2)
        return "late markdown"

    async def fail_summary(*args, **kwargs):
        raise AssertionError("summary model should not start after the research deadline")

    monkeypatch.setattr(service.router, "search", fake_search)
    monkeypatch.setattr(service.extractor, "extract", slow_extract)
    monkeypatch.setattr(service, "_call_summary_model", fail_summary)

    started = time.perf_counter()
    result = asyncio.run(service.research("q", screenshot_mode="never", include_markdown=True))
    elapsed = time.perf_counter() - started

    assert elapsed < 0.12
    assert result.success is True
    assert result.partial is True
    assert result.degraded is True
    assert result.error == "研究总预算耗尽"
    assert result.contexts[0].markdown == "usable search snippet"


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


def test_research_rejects_non_text_extraction_and_uses_snippet(monkeypatch):
    service = SummaryService(
        Settings(
            gateway_api_key="test",
            screenshot_min_markdown_chars=100,
            summary_fallback_enabled=True,
        )
    )
    captured = {}

    async def fake_search(*args, **kwargs):
        from app.schemas.common import SearchResult
        from app.schemas.search import SearchResponse

        return SearchResponse(
            success=True,
            provider="brave",
            query="q",
            cached=False,
            results=[
                SearchResult(
                    title="A",
                    url="https://example.com/a",
                    snippet=(
                        "This search result describes a complete article with enough readable context "
                        "to support a bounded fallback when the extraction provider returns only images."
                    ),
                )
            ],
        )

    async def fake_extract(url):
        return "![proxy](https://images.example.net/" + ("x" * 500) + ".png)"

    async def fake_summary(query, context, sources):
        captured["context"] = context
        return "summary"

    monkeypatch.setattr(service.router, "search", fake_search)
    monkeypatch.setattr(service.extractor, "extract", fake_extract)
    monkeypatch.setattr(service, "_call_summary_model", fake_summary)

    result = asyncio.run(service.research("q", screenshot_mode="never", include_markdown=True))

    assert result.success is True
    assert result.partial is True
    assert result.contexts[0].status == "snippet"
    assert result.contexts[0].extracted is False
    assert result.contexts[0].quality == "usable"
    assert result.contexts[0].markdown.startswith("This search result")
    assert "images.example.net" not in captured["context"]
    assert "正文质量" in result.contexts[0].error


def test_research_returns_phase_timings(monkeypatch):
    service = SummaryService(
        Settings(
            gateway_api_key="test",
            screenshot_min_markdown_chars=50,
            summary_fallback_enabled=True,
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
            results=[SearchResult(title="A", url="https://example.com/a", snippet="fallback snippet")],
        )

    async def fake_extract(url):
        return "Readable article content. " * 20

    async def fake_summary(*args, **kwargs):
        return "summary"

    monkeypatch.setattr(service.router, "search", fake_search)
    monkeypatch.setattr(service.extractor, "extract", fake_extract)
    monkeypatch.setattr(service, "_call_summary_model", fake_summary)

    result = asyncio.run(service.research("q", screenshot_mode="never"))

    assert result.timings.search_ms >= 0
    assert result.timings.extract_ms >= 0
    assert result.timings.summary_ms >= 0
    assert result.timings.total_ms >= 0


def test_research_screenshot_fallback_respects_overall_deadline(monkeypatch):
    service = SummaryService(
        Settings(
            gateway_api_key="test",
            research_timeout_seconds=0.03,
            request_timeout_seconds=0.01,
            screenshot_min_markdown_chars=100,
            summary_fallback_enabled=True,
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
            results=[SearchResult(title="A", url="https://example.com/a", snippet="")],
        )

    async def fake_extract(url):
        return "![proxy](https://images.example.net/image.png)"

    async def slow_capture(*args, **kwargs):
        await asyncio.sleep(0.2)
        raise AssertionError("research deadline was not enforced during screenshot fallback")

    monkeypatch.setattr(service.router, "search", fake_search)
    monkeypatch.setattr(service.extractor, "extract", fake_extract)
    monkeypatch.setattr(service.screenshots, "capture", slow_capture)

    started = time.perf_counter()
    result = asyncio.run(service.research("q", screenshot_mode="auto", include_markdown=True))
    elapsed = time.perf_counter() - started

    assert elapsed < 0.1
    assert result.success is True
    assert result.partial is True
    assert result.contexts[0].status == "error"
    assert result.error == "研究总预算耗尽"
