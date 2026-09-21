from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from app.config import Settings
from app.schemas.common import SearchResult
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call
from app.utils.logging import logger

T = TypeVar("T")


class AnySearchProvider:
    name = "anysearch"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(
        self,
        query: str,
        max_results: int,
        *,
        tag: str | None = None,
        params: dict[str, Any] | None = None,
        zone: str | None = None,
        language: str | None = None,
    ) -> list[SearchResult]:
        payload: dict[str, Any] = {"query": query, "max_results": max_results}
        if tag:
            payload["tag"] = tag
        if params:
            payload["params"] = params
        if zone:
            payload["zone"] = zone
        if language:
            payload["language"] = language
        return await self._with_keys(
            "search",
            lambda key: self._search_request(key, payload, max_results),
        )

    async def sub_domains(self, domains: list[str]) -> list[dict[str, Any]]:
        payload = await self._with_keys(
            "sub-domains",
            lambda key: self._sub_domains_request(key, domains),
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict) and isinstance(data.get("domains"), list):
            return data["domains"]
        return []

    async def _search_request(
        self,
        api_key: str,
        payload: dict[str, Any],
        max_results: int,
    ) -> list[SearchResult]:
        response = await self._request("POST", "/v1/search", api_key, json=payload)
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        raw_results = data.get("results") if isinstance(data, dict) else []
        if not isinstance(raw_results, list):
            raise GatewayError("AnySearch 返回无效搜索结果", status_code=502)

        results: list[SearchResult] = []
        for item in raw_results[:max_results]:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not isinstance(url, str) or not url.strip():
                continue
            results.append(
                SearchResult(
                    title=str(item.get("title") or url).strip()[:500],
                    url=url.strip(),
                    snippet=str(item.get("content") or item.get("snippet") or "").strip()[:5000],
                    provider_metadata={
                        key: item[key]
                        for key in ("tag", "sub_domain", "domain")
                        if isinstance(item.get(key), str)
                    },
                )
            )
        return results

    async def _sub_domains_request(self, api_key: str, domains: list[str]) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/v1/sub-domains",
            api_key,
            params=[("domain", domain) for domain in domains],
        )

    async def _request(self, method: str, path: str, api_key: str, **kwargs: Any) -> dict[str, Any]:
        async with build_client(self.settings, timeout=self.settings.anysearch_timeout_seconds) as client:
            response = await client.request(
                method,
                f"{self.settings.anysearch_api_url}{path}",
                headers=self._headers(api_key),
                **kwargs,
            )
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError as exc:
                raise GatewayError("AnySearch 返回无效 JSON 响应", status_code=502) from exc

        if not isinstance(payload, dict) or payload.get("code", 0) != 0:
            raise GatewayError(
                "AnySearch 上游拒绝请求",
                status_code=502,
                detail={
                    "retryable": True,
                    "anysearch_code": payload.get("code") if isinstance(payload, dict) else None,
                },
            )
        return payload

    async def _with_keys(self, operation: str, request: Callable[[str], Awaitable[T]]) -> T:
        if not self.settings.anysearch_enabled:
            raise GatewayError("AnySearch 未启用", status_code=500)
        keys = self.configured_api_keys(self.settings) or [""]
        last_error: GatewayError | None = None
        for index, api_key in enumerate(keys, start=1):
            try:
                return await timed_call(
                    f"AnySearch[{operation}][{index}]",
                    lambda api_key=api_key: request(api_key),
                )
            except GatewayError as exc:
                last_error = exc
                if not self._should_try_next_key(exc) or index >= len(keys):
                    raise
                logger.warning("AnySearch {} upstream {} 暂不可用，尝试下一枚 key", operation, index)
        raise last_error or GatewayError("AnySearch 调用失败", status_code=502)

    @staticmethod
    def _headers(api_key: str) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Anysearch-Client": "search-gateway/1.0",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    @classmethod
    def configured_api_keys(cls, settings: Settings) -> list[str]:
        keys: list[str] = []
        for raw in [settings.anysearch_api_key, *settings.anysearch_api_keys.split(",")]:
            key = raw.strip()
            if key and key not in keys:
                keys.append(key)
        return keys[:2]

    @classmethod
    def configured_upstream_count(cls, settings: Settings) -> int:
        return len(cls.configured_api_keys(settings))

    @staticmethod
    def _should_try_next_key(error: GatewayError) -> bool:
        if error.status_code == 504:
            return True
        detail = error.detail if isinstance(error.detail, dict) else {}
        if detail.get("retryable") is True or isinstance(detail.get("error_type"), str):
            return True
        status = detail.get("status")
        return isinstance(status, int) and (status in {401, 402, 403, 429} or status >= 500)
