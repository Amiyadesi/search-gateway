from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.schemas.common import SearchResult
from app.utils.http import build_client
from app.utils.logging import logger


@dataclass(frozen=True)
class RerankOutcome:
    results: list[SearchResult]
    applied: bool
    succeeded: bool
    error: str | None = None


class RerankService:
    """OpenAI-compatible rerank 后处理；失败时保留原始搜索顺序。"""

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(
            self.settings.rerank_enabled
            and self.settings.rerank_api_key
            and self.settings.rerank_base_url
            and self.settings.rerank_model
        )

    async def rerank(self, query: str, results: list[SearchResult]) -> list[SearchResult]:
        return (await self.rerank_with_status(query, results)).results

    async def rerank_with_status(self, query: str, results: list[SearchResult]) -> RerankOutcome:
        if not self.enabled or len(results) <= 1:
            return RerankOutcome(results=results, applied=False, succeeded=True)

        top_n = max(1, min(self.settings.rerank_top_n, len(results)))
        documents = [self._document_text(item) for item in results[:top_n]]
        try:
            async with build_client(self.settings, timeout=self.settings.rerank_timeout_seconds) as client:
                resp = await client.post(
                    f"{self.settings.rerank_base_url}/rerank",
                    headers={
                        "Authorization": f"Bearer {self.settings.rerank_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.settings.rerank_model,
                        "query": query,
                        "documents": documents,
                        "top_n": top_n,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            ranked = self._apply_rankings(results, data, top_n)
            return RerankOutcome(results=ranked or results, applied=True, succeeded=True)
        except Exception as exc:
            logger.warning("Rerank 调用失败，保留原始排序: {}", type(exc).__name__)
            return RerankOutcome(
                results=results,
                applied=True,
                succeeded=False,
                error=type(exc).__name__,
            )

    @staticmethod
    def _document_text(result: SearchResult) -> str:
        return f"{result.title}\n{result.url}\n{result.snippet}".strip()

    @classmethod
    def _apply_rankings(cls, results: list[SearchResult], data: Any, top_n: int) -> list[SearchResult]:
        rankings = cls._ranking_items(data)
        if not rankings:
            return []

        used: set[int] = set()
        ordered: list[SearchResult] = []
        for item in rankings:
            index = cls._index_from_item(item)
            if index is None or index < 0 or index >= top_n or index in used:
                continue
            ordered.append(results[index])
            used.add(index)

        for index, result in enumerate(results[:top_n]):
            if index not in used:
                ordered.append(result)
        ordered.extend(results[top_n:])
        return ordered

    @staticmethod
    def _ranking_items(data: Any) -> list[Any]:
        if isinstance(data, list):
            return data
        if not isinstance(data, dict):
            return []
        for key in ("results", "data", "rankings"):
            value = data.get(key)
            if isinstance(value, list):
                return value
        return []

    @staticmethod
    def _index_from_item(item: Any) -> int | None:
        if isinstance(item, int):
            return item
        if not isinstance(item, dict):
            return None
        for key in ("index", "document_index", "documentIndex"):
            value = item.get(key)
            if isinstance(value, int):
                return value
        document = item.get("document")
        if isinstance(document, dict):
            value = document.get("index")
            if isinstance(value, int):
                return value
        return None
