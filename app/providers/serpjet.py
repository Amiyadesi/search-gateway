from typing import Any

from app.config import Settings
from app.schemas.common import SearchResult
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call
from app.utils.logging import logger
from app.utils.url_normalization import normalize_url


SERPJET_SEARCH_URL = "https://api.serpjet.io/v1/search"


class SerpJetProvider:
    name = "serpjet"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        api_keys = self.configured_api_keys(self.settings)
        if not api_keys:
            raise GatewayError("SerpJet API Key 未配置", status_code=500)

        last_error: GatewayError | None = None
        for index, api_key in enumerate(api_keys, start=1):
            try:
                return await timed_call(
                    f"SerpJet[{index}]",
                    lambda api_key=api_key: self._request(query, max_results, api_key),
                )
            except GatewayError as exc:
                last_error = exc
                if not self._should_try_next_key(exc) or index >= len(api_keys):
                    raise
                logger.warning("SerpJet upstream {} 暂不可用，尝试下一枚 key", index)

        if last_error:
            raise last_error
        return []

    async def _request(self, query: str, max_results: int, api_key: str) -> list[SearchResult]:
        async with build_client(self.settings, timeout=self.settings.serpjet_timeout_seconds) as client:
            response = await client.get(
                SERPJET_SEARCH_URL,
                headers={"X-API-KEY": api_key, "Accept": "application/json"},
                params={
                    "q": query,
                    "engine": "google",
                    "type": "search",
                    "num": max_results,
                },
            )
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError as exc:
                raise GatewayError("SerpJet 返回无效 JSON 响应", status_code=502) from exc
        return self._parse_results(payload, max_results)

    @classmethod
    def _parse_results(cls, payload: Any, max_results: int) -> list[SearchResult]:
        if not isinstance(payload, dict) or not isinstance(payload.get("organic"), list):
            raise GatewayError("SerpJet 返回无效响应", status_code=502)
        results: list[SearchResult] = []
        for item in payload["organic"]:
            if not isinstance(item, dict):
                continue
            url = normalize_url(cls._text(item.get("link"), 2048))
            if not url:
                continue
            title = cls._text(item.get("title"), 500)
            snippet = cls._text(item.get("snippet"), 1200)
            position = cls._position(item.get("position"))
            metadata: dict[str, Any] = {"engine": "google"}
            if position is not None:
                metadata["position"] = position
            results.append(
                SearchResult(
                    title=title or url,
                    url=url,
                    snippet=snippet,
                    provider_metadata=metadata,
                )
            )
            if len(results) >= max(1, max_results):
                break
        return results

    @staticmethod
    def configured_api_keys(settings: Settings) -> list[str]:
        keys: list[str] = []
        for raw in settings.serpjet_api_keys.split(","):
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
        if isinstance(detail.get("error_type"), str):
            return True
        status = detail.get("status")
        return isinstance(status, int) and (status in {401, 402, 403, 429} or status >= 500)

    @staticmethod
    def _text(value: Any, maximum: int) -> str:
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            return ""
        return " ".join(str(value).split())[:maximum]

    @staticmethod
    def _position(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            position = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return position if position > 0 else None
