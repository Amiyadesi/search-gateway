from app.config import Settings
from app.providers.firecrawl import FirecrawlProvider
from app.schemas.common import SearchResult
from app.schemas.screenshot import ScreenshotMetadata, ScreenshotRequest
from app.schemas.summary import ResearchContext, ResearchResponse, SummaryResponse, UrlAnalysisResponse
from app.services.router_service import RouterService
from app.services.screenshot_service import ScreenshotService
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call
from app.utils.logging import logger


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
            logger.warning("页面提取失败，尝试截图兜底: {} {}", url, exc.message)
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
        search_response = await self.router.search(
            query,
            provider=provider,
            max_results=max_results or self.settings.max_search_results,
        )
        sources = search_response.results[: (max_sources or self.settings.summary_max_sources)]
        context_items = await self._collect_context_items(sources, screenshot_mode=screenshot_mode)
        contexts = self._contexts_to_prompt(context_items)
        prompt_context = self._truncate_context("\n\n".join(contexts))
        screenshots = self._context_screenshots(context_items)

        try:
            summary = await self._call_summary_model(query, prompt_context, sources)
            return ResearchResponse(
                success=True,
                provider=search_response.provider,
                query=query,
                summary=summary,
                sources=sources,
                contexts=self._response_contexts(context_items, include_markdown),
                screenshots=screenshots,
            )
        except GatewayError as exc:
            if not self.settings.summary_fallback_enabled:
                raise
            logger.warning("SummaryModel 不可用，返回研究降级总结: {}", exc.message)
            fallback = self._build_fallback_summary(query, contexts, sources, exc.message)
            return ResearchResponse(
                success=True,
                provider=search_response.provider,
                query=query,
                summary=fallback,
                sources=sources,
                contexts=self._response_contexts(context_items, include_markdown),
                screenshots=screenshots,
                degraded=True,
                error=exc.message,
            )

    async def _collect_context_items(
        self,
        sources: list[SearchResult],
        screenshot_mode: str = "auto",
    ) -> list[ResearchContext]:
        contexts: list[ResearchContext] = []
        for item in sources:
            markdown = ""
            extracted = False
            error = None
            try:
                markdown = await self.extractor.extract(str(item.url))
                extracted = True
            except Exception as exc:
                error = str(exc)
                logger.warning("提取失败，改用搜索摘要: {} {}", item.url, exc)
                if item.snippet:
                    markdown = item.snippet
            screenshot = await self._maybe_capture(str(item.url), markdown, not extracted, screenshot_mode)
            if not markdown and screenshot:
                markdown = self._screenshot_markdown(screenshot)
            contexts.append(
                ResearchContext(
                    title=item.title,
                    url=str(item.url),
                    markdown=markdown,
                    extracted=extracted,
                    screenshot=screenshot,
                    error=error,
                )
            )
        return contexts

    @staticmethod
    def _contexts_to_prompt(contexts: list[ResearchContext]) -> list[str]:
        return [
            f"# {item.title}\nURL: {item.url}\n\n{item.markdown}"
            for item in contexts
            if item.markdown
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
    ) -> ScreenshotMetadata | None:
        if screenshot_mode == "never":
            return None
        compact = " ".join(markdown.split())
        should_capture = screenshot_mode == "force" or (
            screenshot_mode == "auto"
            and (extraction_failed or len(compact) < self.settings.screenshot_min_markdown_chars)
        )
        if not should_capture:
            return None
        return await self.screenshots.capture(ScreenshotRequest(url=url))

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
            return data["choices"][0]["message"]["content"]

        last_error: GatewayError | None = None
        retry_attempts = max(1, self.settings.summary_retry_attempts)
        for attempt in range(1, retry_attempts + 1):
            try:
                return await timed_call("SummaryModel", request)
            except GatewayError as exc:
                last_error = exc
                detail = exc.detail if isinstance(exc.detail, dict) else {}
                if attempt >= retry_attempts or exc.status_code not in {502, 504}:
                    raise
                logger.warning(
                    "SummaryModel 重试 {}/{}，原因: {} {}",
                    attempt + 1,
                    retry_attempts,
                    exc.message,
                    detail.get("error_type") or detail.get("status") or "unknown",
                )

        assert last_error is not None
        raise last_error

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
