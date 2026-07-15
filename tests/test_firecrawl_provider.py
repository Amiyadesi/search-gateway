import asyncio

from app.config import Settings
from app.providers.firecrawl import FirecrawlProvider


def test_firecrawl_extract_document_preserves_markdown_and_canonical(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "markdown": "# Page",
                    "rawHtml": '<link rel="canonical" href="/canonical/?utm_source=x">',
                    "metadata": {},
                }
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

    monkeypatch.setattr("app.providers.firecrawl.build_client", lambda *_args, **_kwargs: FakeClient())
    provider = FirecrawlProvider(
        Settings(
            gateway_api_key="test",
            firecrawl_api_key="configured",
            firecrawl_api_url="https://extract.example/v2",
        )
    )

    document = asyncio.run(provider.extract_document("https://example.com/page"))

    assert calls[0][0] == "https://extract.example/v2/scrape"
    assert calls[0][1]["json"]["formats"] == ["markdown", "rawHtml"]
    assert document.markdown == "# Page"
    assert document.canonical_url == "https://example.com/canonical"


def test_legacy_firecrawl_extract_still_returns_string(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"markdown": "legacy"}}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr("app.providers.firecrawl.build_client", lambda *_args, **_kwargs: FakeClient())
    provider = FirecrawlProvider(Settings(gateway_api_key="test", firecrawl_api_key="configured"))

    assert asyncio.run(provider.extract("https://example.com")) == "legacy"
