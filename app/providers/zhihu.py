import html
import re
import time
from typing import Any

from app.config import Settings
from app.schemas.common import SearchResult
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call
from app.utils.url_normalization import normalize_url


ZHIHU_GLOBAL_SEARCH_URL = "https://developer.zhihu.com/api/v1/content/global_search"
_TAG_PATTERN = re.compile(r"<[^>]+>")


class ZhihuProvider:
    name = "zhihu"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        if not self.settings.zhihu_api_key:
            raise GatewayError("Zhihu API Key 未配置", status_code=500)

        async def request() -> list[SearchResult]:
            async with build_client(self.settings, timeout=self.settings.zhihu_timeout_seconds) as client:
                response = await client.get(
                    ZHIHU_GLOBAL_SEARCH_URL,
                    headers={
                        "Authorization": f"Bearer {self.settings.zhihu_api_key}",
                        "X-Request-Timestamp": str(int(time.time())),
                        "Content-Type": "application/json",
                    },
                    params={
                        "Query": query,
                        "Count": max(1, min(20, max_results)),
                        "SearchDB": "all",
                    },
                )
                response.raise_for_status()
                try:
                    data = response.json()
                except ValueError as exc:
                    raise GatewayError("Zhihu 返回无效 JSON 响应", status_code=502) from exc
            return self._parse_results(data, max_results)

        return await timed_call("Zhihu", request)

    @classmethod
    def _parse_results(cls, payload: Any, max_results: int) -> list[SearchResult]:
        if not isinstance(payload, dict):
            raise GatewayError("Zhihu 返回无效响应", status_code=502)
        code = payload.get("Code")
        if code is None:
            raise GatewayError("Zhihu 返回无效响应", status_code=502)
        if code != 0:
            raise GatewayError(
                "Zhihu 搜索请求失败",
                status_code=502,
                detail={"upstream_code": str(code)[:32]},
            )
        data = payload.get("Data")
        items = data.get("Items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise GatewayError("Zhihu 返回无效响应", status_code=502)

        results: list[SearchResult] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            observed_url = cls._bounded_text(item.get("Url"), 2048)
            url = normalize_url(observed_url)
            if not url:
                continue
            title = cls._plain_text(item.get("Title"))
            snippet = cls._plain_text(item.get("ContentText"))
            if not title and not snippet:
                continue
            metadata = cls._provider_metadata(item, observed_url)
            results.append(
                SearchResult(
                    title=title or url,
                    url=url,
                    snippet=snippet[:1200],
                    provider_metadata=metadata,
                )
            )
            if len(results) >= max(1, max_results):
                break
        return results

    @staticmethod
    def _plain_text(value: Any) -> str:
        if not isinstance(value, str):
            return ""
        without_tags = _TAG_PATTERN.sub("", html.unescape(value))
        return " ".join(without_tags.split())

    @classmethod
    def _provider_metadata(cls, item: dict[str, Any], observed_url: str) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        text_fields = {
            "content_type": (item.get("ContentType"), 80),
            "content_id": (item.get("ContentID"), 160),
            "author_name": (item.get("AuthorName"), 200),
            "author_badge_text": (item.get("AuthorBadgeText"), 300),
            "authority_level": (item.get("AuthorityLevel"), 32),
        }
        for key, (value, maximum) in text_fields.items():
            text = cls._bounded_text(value, maximum)
            if text:
                metadata[key] = text

        for key, value in {
            "edit_time": item.get("EditTime"),
            "comment_count": item.get("CommentCount"),
            "vote_up_count": item.get("VoteUpCount"),
        }.items():
            number = cls._non_negative_int(value)
            if number is not None:
                metadata[key] = number

        if observed_url:
            metadata["observed_url"] = observed_url
        return metadata

    @staticmethod
    def _bounded_text(value: Any, maximum: int) -> str:
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            return ""
        return " ".join(str(value).split())[:maximum]

    @staticmethod
    def _non_negative_int(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if number >= 0 else None
