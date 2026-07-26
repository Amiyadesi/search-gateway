import asyncio
import time
import uuid

from app.config import Settings
from app.providers.firecrawl import FirecrawlProvider
from app.schemas.common import SearchResult
from app.schemas.screenshot import ScreenshotMetadata, ScreenshotRequest
from app.schemas.summary import (
    ResearchContext,
    ResearchResponse,
    ResearchTimings,
    SummaryResponse,
    UrlAnalysisResponse,
)
from app.services.router_service import RouterService
from app.services.screenshot_service import ScreenshotService
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call
from app.utils.logging import logger
from app.utils.markdown_quality import MarkdownQuality, assess_markdown_quality


class SummaryService:
    """搜索、抓取、拼接上下文，再调用 OpenAI-compatible 模型总结。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.router = RouterService(settings)
        self.extractor = FirecrawlProvider(settings)
        self.screenshots = ScreenshotService(settings)

    async def summarize(
        self,
        query: str,
        provider: str = "auto",
        max_results: int | None = None,
        max_sources: int | None = None,
        screenshot_mode: str = "auto",
    ) -> SummaryResponse:
        search_response = await self.router.search(
            query,
            provider=provider,
            max_results=max_results or self.settings.max_search_results,
        )
        sources = search_response.results[: (max_sources or self.settings.summary_max_sources)]
        context_items = await self._collect_context_items(sources, screenshot_mode=screenshot_mode)
        contexts = self._contexts_to_prompt(context_items)
        screenshots = self._context_screenshots(context_items)

        prompt_context = self._truncate_context("\n\n".join(contexts))
        try:
            summary = await self._call_summary_model(query, prompt_context, sources)
            return SummaryResponse(success=True, summary=summary, sources=sources, screenshots=screenshots)
        except GatewayError as exc:
            if not self.settings.summary_fallback_enabled:
                raise
            logger.warning("SummaryModel 不可用，返回降级总结: {}", exc.message)
            fallback = self._build_fallback_summary(query, contexts, sources, exc.message)
            return SummaryResponse(
                success=True,
                summary=fallback,
                sources=sources,
                screenshots=screenshots,
                degraded=True,
                error=exc.message,
            )

    async def analyze_url(self, url: str, question: str, screenshot_mode: str = "auto") -> UrlAnalysisResponse:
        markdown = ""
        degraded = False
        error = None
        try:
            markdown = await self.extractor.extract(url)
        except GatewayError as exc:
            if screenshot_mode == "never":
                raise
            degraded = True
            error = exc.message
            logger.warning("页面提取失败，尝试截图兜底: {}", exc.message)
        screenshot = await self._maybe_capture(url, markdown, degraded, screenshot_mode)
        if not markdown and screenshot:
            markdown = self._screenshot_markdown(screenshot)
        source = SearchResult(
            title=url,
            url=url,
            snippet=self._snippet(markdown),
        )
        prompt_context = self._truncate_context(f"# {url}\nURL: {url}\n\n{markdown}")
        try:
            analysis = await self._call_summary_model(question, prompt_context, [source])
            return UrlAnalysisResponse(
                success=True,
                url=url,
                analysis=analysis,
                markdown=markdown,
                screenshot=screenshot,
                degraded=degraded or bool(screenshot and screenshot.degraded),
                error=error,
            )
        except GatewayError as exc:
            if not self.settings.summary_fallback_enabled:
                raise
            logger.warning("SummaryModel 不可用，返回页面提取降级分析: {}", exc.message)
            analysis = self._build_fallback_summary(question, [prompt_context], [source], exc.message)
            return UrlAnalysisResponse(
                success=True,
                url=url,
                analysis=analysis,
                markdown=markdown,
                screenshot=screenshot,
                degraded=True,
                error=error or exc.message,
            )

    async def research(
        self,
        query: str,
        provider: str = "auto",
        max_results: int | None = None,
        max_sources: int | None = None,
        include_markdown: bool = False,
        screenshot_mode: str = "auto",
    ) -> ResearchResponse:
        request_started = time.perf_counter()
        request_id = uuid.uuid4().hex[:12]
        deadline = time.monotonic() + self.settings.research_timeout_seconds
        search_started = time.perf_counter()
        try:
            async with asyncio.timeout(max(0.001, deadline - time.monotonic())):
                search_response = await self.router.search(
                    query,
                    provider=provider,
                    max_results=max_results or self.settings.max_search_results,
                )
        except TimeoutError as exc:
            search_ms = self._elapsed_ms(search_started)
            self._log_research_phase(request_id, "search", search_ms, "timeout", True)
            raise GatewayError(
                "研究检索超时",
                status_code=504,
                detail={
                    "code": "RESEARCH_TIMEOUT",
                    "retryable": True,
                    "phase": "search",
                },
            ) from exc
        search_ms = self._elapsed_ms(search_started)
        self._log_research_phase(request_id, "search", search_ms, "complete", False)
        sources = search_response.results[: (max_sources or self.settings.summary_max_sources)]

        extract_started = time.perf_counter()
        context_items = await self._collect_context_items(
            sources,
            screenshot_mode=screenshot_mode,
            deadline=deadline,
        )
        extract_ms = self._elapsed_ms(extract_started)
        contexts = self._contexts_to_prompt(context_items)
        prompt_context = self._truncate_context("\n\n".join(contexts))
        screenshots = self._context_screenshots(context_items)
        partial = any(item.status != "complete" for item in context_items)
        self._log_research_phase(
            request_id,
            "extract",
            extract_ms,
            "partial" if partial else "complete",
            partial,
        )

        model_error: GatewayError | None = None
        summary: str | None = None
        summary_started = time.perf_counter()
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            async with asyncio.timeout(remaining):
                summary = await self._call_summary_model(query, prompt_context, sources)
        except TimeoutError:
            model_error = GatewayError(
                "研究总预算耗尽",
                status_code=504,
                detail={"code": "RESEARCH_TIMEOUT", "retryable": True, "phase": "summary"},
            )
        except GatewayError as exc:
            model_error = exc

        summary_ms = self._elapsed_ms(summary_started)
        if model_error is None:
            assert summary is not None
            timings = self._research_timings(search_ms, extract_ms, summary_ms, request_started)
            self._log_research_phase(request_id, "summary", summary_ms, "complete", partial)
            self._log_research_phase(request_id, "total", timings.total_ms, "complete", partial)
            return ResearchResponse(
                success=True,
                provider=search_response.provider,
                query=query,
                summary=summary,
                sources=sources,
                contexts=self._response_contexts(context_items, include_markdown),
                screenshots=screenshots,
                timings=timings,
                partial=partial,
                degraded=partial,
                error="部分来源未完成正文提取" if partial else None,
            )

        if not self.settings.summary_fallback_enabled:
            self._log_research_phase(request_id, "summary", summary_ms, "error", True)
            raise model_error
        logger.warning("SummaryModel 不可用，返回研究降级总结: {}", model_error.message)
        fallback = self._build_fallback_summary(query, contexts, sources, model_error.message)
        timings = self._research_timings(search_ms, extract_ms, summary_ms, request_started)
        self._log_research_phase(request_id, "summary", summary_ms, "fallback", True)
        self._log_research_phase(request_id, "total", timings.total_ms, "partial", True)
        return ResearchResponse(
            success=True,
            provider=search_response.provider,
            query=query,
            summary=fallback,
            sources=sources,
            contexts=self._response_contexts(context_items, include_markdown),
            screenshots=screenshots,
            timings=timings,
            partial=partial,
            degraded=True,
            error=model_error.message,
        )

    async def _collect_context_items(
        self,
        sources: list[SearchResult],
        screenshot_mode: str = "auto",
        deadline: float | None = None,
    ) -> list[ResearchContext]:
        semaphore = asyncio.Semaphore(max(1, self.settings.evidence_extract_concurrency))

        async def collect(item: SearchResult) -> ResearchContext:
            async with semaphore:
                started = time.perf_counter()
                markdown = ""
                extracted = False
                error = None
                status = "complete"
                quality = assess_markdown_quality("", self.settings.screenshot_min_markdown_chars)
                try:
                    extract_timeout = self.settings.request_timeout_seconds
                    if deadline is not None:
                        extract_timeout = min(extract_timeout, max(0.001, deadline - time.monotonic()))
                    async with asyncio.timeout(max(0.001, extract_timeout)):
                        markdown = await self.extractor.extract(str(item.url))
                    extracted = True
                    quality = assess_markdown_quality(
                        markdown,
                        self.settings.screenshot_min_markdown_chars,
                    )
                    if quality.status != "usable":
                        extracted = False
                        error = (
                            "提取正文质量不足，已改用搜索摘要"
                            if item.snippet
                            else "提取正文质量不足"
                        )
                        status = "snippet" if item.snippet else "error"
                        markdown = item.snippet or ""
                        quality = self._assess_snippet(markdown)
                except TimeoutError:
                    status = "timeout"
                    error = "页面正文提取超时"
                    logger.warning("提取超时，改用搜索摘要")
                    if item.snippet:
                        markdown = item.snippet
                    quality = self._assess_snippet(markdown)
                except GatewayError as exc:
                    status = "error"
                    error = exc.message
                    logger.warning("提取失败，改用搜索摘要: {}", exc.message)
                    if item.snippet:
                        markdown = item.snippet
                    quality = self._assess_snippet(markdown)
                except Exception as exc:
                    status = "error"
                    error = "页面正文提取失败"
                    logger.warning("提取失败，改用搜索摘要: {}", type(exc).__name__)
                    if item.snippet:
                        markdown = item.snippet
                    quality = self._assess_snippet(markdown)
                fallback_is_usable = not extracted and quality.status == "usable"
                screenshot = await self._maybe_capture(
                    str(item.url),
                    markdown,
                    not extracted and not fallback_is_usable,
                    screenshot_mode,
                    deadline=deadline,
                    content_usable=quality.status == "usable",
                )
                if not markdown and screenshot:
                    markdown = self._screenshot_markdown(screenshot)
                return ResearchContext(
                    title=item.title,
                    url=str(item.url),
                    markdown=markdown,
                    extracted=extracted,
                    status=status,
                    quality=quality.status,
                    elapsed_ms=max(0, round((time.perf_counter() - started) * 1000)),
                    screenshot=screenshot,
                    error=error,
                )

        return await asyncio.gather(*(collect(item) for item in sources))

    def _assess_snippet(self, snippet: str) -> MarkdownQuality:
        minimum = max(60, min(120, self.settings.screenshot_min_markdown_chars))
        return assess_markdown_quality(snippet, minimum)

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return max(0, round((time.perf_counter() - started) * 1000))

    @classmethod
    def _research_timings(
        cls,
        search_ms: int,
        extract_ms: int,
        summary_ms: int,
        request_started: float,
    ) -> ResearchTimings:
        return ResearchTimings(
            search_ms=search_ms,
            extract_ms=extract_ms,
            summary_ms=summary_ms,
            total_ms=cls._elapsed_ms(request_started),
        )

    @staticmethod
    def _log_research_phase(
        request_id: str,
        phase: str,
        elapsed_ms: int,
        status: str,
        partial: bool,
    ) -> None:
        logger.info(
            "research_phase request_id={} phase={} elapsed_ms={} status={} partial={}",
            request_id,
            phase,
            elapsed_ms,
            status,
            partial,
        )

    @staticmethod
    def _contexts_to_prompt(contexts: list[ResearchContext]) -> list[str]:
        return [
            f"# {item.title}\nURL: {item.url}\n\n{item.markdown}"
            for item in contexts
            if item.markdown and item.quality == "usable"
        ]

    @staticmethod
    def _response_contexts(contexts: list[ResearchContext], include_markdown: bool) -> list[ResearchContext]:
        if include_markdown:
            return contexts
        return [
            ResearchContext(
                title=item.title,
                url=item.url,
                markdown="",
                extracted=item.extracted,
                status=item.status,
                quality=item.quality,
                elapsed_ms=item.elapsed_ms,
                screenshot=item.screenshot,
                error=item.error,
            )
            for item in contexts
        ]

    @staticmethod
    def _context_screenshots(contexts: list[ResearchContext]) -> list[ScreenshotMetadata]:
        return [item.screenshot for item in contexts if item.screenshot is not None]

    async def _maybe_capture(
        self,
        url: str,
        markdown: str,
        extraction_failed: bool,
        screenshot_mode: str,
        deadline: float | None = None,
        content_usable: bool | None = None,
    ) -> ScreenshotMetadata | None:
        if screenshot_mode == "never":
            return None
        compact = " ".join(markdown.split())
        if content_usable is None:
            content_usable = len(compact) >= self.settings.screenshot_min_markdown_chars
        should_capture = screenshot_mode == "force" or (
            screenshot_mode == "auto"
            and (extraction_failed or not content_usable)
        )
        if not should_capture:
            return None
        request = ScreenshotRequest(url=url)
        if deadline is None:
            return await self.screenshots.capture(request)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return ScreenshotMetadata(provider="auto", degraded=True, error="研究总预算耗尽")
        try:
            async with asyncio.timeout(remaining):
                return await self.screenshots.capture(request)
        except TimeoutError:
            logger.warning("研究总预算耗尽，停止截图兜底")
            return ScreenshotMetadata(provider="auto", degraded=True, error="研究总预算耗尽")

    @staticmethod
    def _screenshot_markdown(screenshot: ScreenshotMetadata) -> str:
        if screenshot.degraded:
            return f"截图兜底失败：{screenshot.error or 'unknown error'}"
        return f"截图兜底：页面正文不可用，已缓存截图 {screenshot.image_url or screenshot.cache_id}。"

    @staticmethod
    def _snippet(markdown: str) -> str:
        compact = " ".join(markdown.split())
        return compact[:500]

    def _truncate_context(self, context: str) -> str:
        max_chars = self.settings.summary_context_max_chars
        if len(context) <= max_chars:
            return context
        return context[:max_chars] + "\n\n[上下文已截断]"

    async def _call_summary_model(self, query: str, context: str, sources: list[SearchResult]) -> str:
        base_url, api_key = self._resolve_summary_endpoint()
        source_lines = "\n".join(f"- {item.title}: {item.url}" for item in sources)
        messages = [
            {
                "role": "system",
                "content": "你是搜索总结助手。请用中文输出，准确、结构清晰、保留关键事实，不编造来源。",
            },
            {
                "role": "user",
                "content": (
                    f"查询：{query}\n\n"
                    f"来源：\n{source_lines}\n\n"
                    f"网页上下文：\n{context}\n\n"
                    "请给出高质量总结，并在最后列出参考来源。"
                ),
            },
        ]

        async def request() -> str:
            payload = {
                "model": self.settings.summary_model,
                "messages": messages,
                "temperature": 0.2,
                "max_tokens": self.settings.summary_model_max_tokens,
            }
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": self.settings.summary_user_agent,
                "Accept": self.settings.summary_accept,
            }
            async with build_client(self.settings, timeout=self.settings.summary_timeout_seconds) as client:
                resp = await client.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
            return self._parse_summary_content(data)

        last_error: GatewayError | None = None
        retry_attempts = max(1, self.settings.summary_retry_attempts)
        for attempt in range(1, retry_attempts + 1):
            try:
                async with asyncio.timeout(max(0.001, self.settings.summary_timeout_seconds)):
                    return await timed_call("SummaryModel", request)
            except TimeoutError:
                current_error = GatewayError(
                    "SummaryModel 调用超时",
                    status_code=504,
                    detail={"code": "SUMMARY_TIMEOUT", "retryable": True, "phase": "model"},
                )
            except GatewayError as exc:
                current_error = exc
            last_error = current_error
            detail = current_error.detail if isinstance(current_error.detail, dict) else {}
            if attempt >= retry_attempts or current_error.status_code not in {502, 504}:
                raise current_error
            logger.warning(
                "SummaryModel 重试 {}/{}，原因: {} {}",
                attempt + 1,
                retry_attempts,
                current_error.message,
                detail.get("error_type") or detail.get("status") or detail.get("code") or "unknown",
            )

        assert last_error is not None
        raise last_error

    @staticmethod
    def _parse_summary_content(data: object) -> str:
        payload = data
        if isinstance(payload, dict) and "choices" not in payload and isinstance(payload.get("data"), dict):
            payload = payload["data"]
        try:
            content = payload["choices"][0]["message"]["content"]
        except (IndexError, KeyError, TypeError) as exc:
            raise GatewayError(
                "SummaryModel 返回格式无效",
                status_code=502,
                detail={"code": "SUMMARY_RESPONSE_INVALID", "retryable": True},
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise GatewayError(
                "SummaryModel 返回格式无效",
                status_code=502,
                detail={"code": "SUMMARY_RESPONSE_INVALID", "retryable": True},
            )
        return content

    @staticmethod
    def _build_fallback_summary(
        query: str,
        contexts: list[str],
        sources: list[SearchResult],
        reason: str,
    ) -> str:
        """上游模型超时也要给 MCP 一个可用结果，避免客户端误判为 transport closed。"""
        source_lines = "\n".join(f"- {item.title}: {item.url}" for item in sources)
        snippets: list[str] = []
        for context in contexts[:3]:
            compact = " ".join(context.split())
            if compact:
                snippets.append(f"- {compact[:500]}")
        snippet_text = "\n".join(snippets) if snippets else "- 暂无可用正文，只返回来源列表。"
        return (
            f"查询：{query}\n\n"
            "AI 总结模型暂时超时，已返回搜索资料降级摘要。你可以稍后重试 /summary，"
            "或先基于以下来源继续阅读。\n\n"
            f"超时原因：{reason}\n\n"
            f"资料要点：\n{snippet_text}\n\n"
            f"参考来源：\n{source_lines}"
        )

    def _resolve_summary_endpoint(self) -> tuple[str, str]:
        if self.settings.summary_provider == "openai":
            base_url = self.settings.summary_base_url or "https://api.openai.com/v1"
            api_key = self.settings.summary_api_key or self.settings.openai_api_key
        elif self.settings.summary_provider == "deepseek":
            base_url = self.settings.summary_base_url or "https://api.deepseek.com/v1"
            api_key = self.settings.summary_api_key or self.settings.deepseek_api_key
        else:
            base_url = self.settings.summary_base_url
            api_key = self.settings.summary_api_key

        if not base_url or not api_key:
            raise GatewayError("总结模型未配置 SUMMARY_BASE_URL 或 SUMMARY_API_KEY", status_code=500)
        return base_url.rstrip("/"), api_key

    async def close(self) -> None:
        await self.router.close()
        await self.screenshots.close()
