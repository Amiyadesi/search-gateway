import asyncio
import time

import httpx
import pytest

from app.config import Settings
from app.providers.zhihu import ZHIHU_GLOBAL_SEARCH_URL, ZhihuProvider
from app.utils.errors import GatewayError


def test_zhihu_provider_maps_results_and_normalizes_tracking_urls(monkeypatch):
    provider = ZhihuProvider(Settings(gateway_api_key="test", zhihu_api_key="secret"))
    captured = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "Code": 0,
                "Message": "success",
                "Data": {
                    "Items": [
                        {
                            "Title": "<em>GEO</em> 实践",
                            "ContentType": "Article",
                            "ContentID": "123",
                            "ContentText": "使用 <em>证据优先</em> 的审计方法。",
                            "Url": "https://zhuanlan.zhihu.com/p/123?utm_medium=openapi_platform&utm_source=test#part",
                            "CommentCount": 7,
                            "VoteUpCount": 11,
                            "AuthorName": "测试作者",
                            "AuthorBadgeText": "创作者",
                            "EditTime": 1748355858,
                            "AuthorityLevel": "2",
                        }
                    ]
                },
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, **kwargs):
            captured.update(url=url, **kwargs)
            return FakeResponse()

    monkeypatch.setattr("app.providers.zhihu.build_client", lambda *_args, **_kwargs: FakeClient())

    before = int(time.time())
    results = asyncio.run(provider.search("GEO 审计", 50))
    after = int(time.time())

    assert captured["url"] == ZHIHU_GLOBAL_SEARCH_URL
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert before <= int(captured["headers"]["X-Request-Timestamp"]) <= after
    assert captured["headers"]["Content-Type"] == "application/json"
    assert captured["params"] == {"Query": "GEO 审计", "Count": 20, "SearchDB": "all"}
    assert len(results) == 1
    assert results[0].title == "GEO 实践"
    assert results[0].snippet == "使用 证据优先 的审计方法。"
    assert str(results[0].url) == "https://zhuanlan.zhihu.com/p/123"
    assert results[0].provider_metadata == {
        "content_type": "Article",
        "content_id": "123",
        "author_name": "测试作者",
        "author_badge_text": "创作者",
        "authority_level": "2",
        "edit_time": 1748355858,
        "comment_count": 7,
        "vote_up_count": 11,
        "observed_url": "https://zhuanlan.zhihu.com/p/123?utm_medium=openapi_platform&utm_source=test#part",
    }


def test_zhihu_provider_rejects_malformed_success_payload(monkeypatch):
    provider = ZhihuProvider(Settings(gateway_api_key="test", zhihu_api_key="secret"))

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"Code": 0, "Data": {"Items": "not-a-list"}}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr("app.providers.zhihu.build_client", lambda *_args, **_kwargs: FakeClient())

    with pytest.raises(GatewayError) as raised:
        asyncio.run(provider.search("test", 5))

    assert raised.value.status_code == 502
    assert "secret" not in raised.value.message


def test_zhihu_provider_requires_official_success_code(monkeypatch):
    provider = ZhihuProvider(Settings(gateway_api_key="test", zhihu_api_key="secret"))

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"Data": {"Items": []}}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr("app.providers.zhihu.build_client", lambda *_args, **_kwargs: FakeClient())

    with pytest.raises(GatewayError) as raised:
        asyncio.run(provider.search("test", 5))

    assert raised.value.status_code == 502
    assert raised.value.detail is None


def test_zhihu_provider_rejects_non_json_response(monkeypatch):
    provider = ZhihuProvider(Settings(gateway_api_key="test", zhihu_api_key="secret"))

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            raise ValueError("not json")

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr("app.providers.zhihu.build_client", lambda *_args, **_kwargs: FakeClient())

    with pytest.raises(GatewayError) as raised:
        asyncio.run(provider.search("test", 5))

    assert raised.value.status_code == 502
    assert "JSON" in raised.value.message
    assert "not json" not in raised.value.message


def test_zhihu_provider_preserves_upstream_auth_status_for_evidence(monkeypatch):
    provider = ZhihuProvider(Settings(gateway_api_key="test", zhihu_api_key="secret"))

    class FakeResponse:
        status_code = 401
        headers = {}

        def raise_for_status(self) -> None:
            request = httpx.Request("GET", ZHIHU_GLOBAL_SEARCH_URL)
            response = httpx.Response(401, request=request, text="invalid credential")
            raise httpx.HTTPStatusError("unauthorized", request=request, response=response)

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr("app.providers.zhihu.build_client", lambda *_args, **_kwargs: FakeClient())

    with pytest.raises(GatewayError) as raised:
        asyncio.run(provider.search("test", 5))

    assert raised.value.status_code == 502
    assert raised.value.detail == {"status": 401}


@pytest.mark.parametrize("status", [429, 503])
def test_zhihu_provider_preserves_quota_and_server_status(monkeypatch, status):
    provider = ZhihuProvider(Settings(gateway_api_key="test", zhihu_api_key="secret"))

    class FakeResponse:
        def raise_for_status(self) -> None:
            request = httpx.Request("GET", ZHIHU_GLOBAL_SEARCH_URL)
            response = httpx.Response(
                status,
                request=request,
                headers={"Retry-After": "17"} if status == 429 else {},
                text="upstream failure",
            )
            raise httpx.HTTPStatusError("failed", request=request, response=response)

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr("app.providers.zhihu.build_client", lambda *_args, **_kwargs: FakeClient())

    with pytest.raises(GatewayError) as raised:
        asyncio.run(provider.search("test", 5))

    assert raised.value.status_code == 502
    assert raised.value.detail["status"] == status
    assert raised.value.detail.get("retry_after_seconds") == (17 if status == 429 else None)
    assert "upstream failure" not in raised.value.message


def test_zhihu_provider_maps_timeout_without_exposing_query_or_key(monkeypatch):
    provider = ZhihuProvider(Settings(gateway_api_key="test", zhihu_api_key="secret"))

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *_args, **_kwargs):
            request = httpx.Request("GET", ZHIHU_GLOBAL_SEARCH_URL)
            raise httpx.ReadTimeout("private query secret", request=request)

    monkeypatch.setattr("app.providers.zhihu.build_client", lambda *_args, **_kwargs: FakeClient())

    with pytest.raises(GatewayError) as raised:
        asyncio.run(provider.search("private query", 5))

    assert raised.value.status_code == 504
    assert "private query" not in raised.value.message
    assert "secret" not in raised.value.message


def test_zhihu_provider_requires_server_side_key():
    provider = ZhihuProvider(Settings(gateway_api_key="test", zhihu_api_key=""))

    with pytest.raises(GatewayError) as raised:
        asyncio.run(provider.search("test", 5))

    assert raised.value.status_code == 500
