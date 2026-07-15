import asyncio

from app.config import Settings
from app.schemas.common import SearchResult
from app.services.rerank_service import RerankService


def test_rerank_disabled_returns_original_results():
    results = [
        SearchResult(title="One", url="https://example.com/1", snippet="first"),
        SearchResult(title="Two", url="https://example.com/2", snippet="second"),
    ]
    service = RerankService(Settings(gateway_api_key="test"))

    assert asyncio.run(service.rerank("query", results)) == results


def test_rerank_success_reorders_results(monkeypatch):
    results = [
        SearchResult(title="One", url="https://example.com/1", snippet="first"),
        SearchResult(title="Two", url="https://example.com/2", snippet="second"),
        SearchResult(title="Three", url="https://example.com/3", snippet="third"),
    ]
    service = RerankService(
        Settings(
            gateway_api_key="test",
            rerank_enabled=True,
            rerank_base_url="https://rerank.example.com/v1",
            rerank_api_key="rr-key",
            rerank_model="rerank-model",
            rerank_top_n=2,
        )
    )
    calls = {"url": "", "json": {}, "headers": {}}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"results": [{"index": 1}, {"index": 0}]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            calls["url"] = url
            calls["json"] = kwargs.get("json") or {}
            calls["headers"] = kwargs.get("headers") or {}
            return FakeResponse()

    monkeypatch.setattr("app.services.rerank_service.build_client", lambda *_args, **_kwargs: FakeClient())
    ranked = asyncio.run(service.rerank("query", results))

    assert calls["url"] == "https://rerank.example.com/v1/rerank"
    assert calls["headers"]["Authorization"] == "Bearer rr-key"
    assert calls["json"]["model"] == "rerank-model"
    assert [item.title for item in ranked] == ["Two", "One", "Three"]


def test_rerank_failure_returns_original_results(monkeypatch):
    results = [
        SearchResult(title="One", url="https://example.com/1", snippet="first"),
        SearchResult(title="Two", url="https://example.com/2", snippet="second"),
    ]
    service = RerankService(
        Settings(gateway_api_key="test", rerank_enabled=True, rerank_api_key="rr-key", rerank_model="rerank-model")
    )

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *_args, **_kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr("app.services.rerank_service.build_client", lambda *_args, **_kwargs: FakeClient())

    assert asyncio.run(service.rerank("query", results)) == results
