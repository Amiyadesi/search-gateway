import asyncio

from app.config import Settings
from app.providers.context7 import Context7Provider


def test_context7_provider_resolves_library_then_fetches_context(monkeypatch):
    provider = Context7Provider(
        Settings(gateway_api_key="test", context7_api_key="ctx", context7_base_url="https://context7.com/api")
    )
    calls = []

    class FakeResponse:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self.data

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, **kwargs):
            calls.append({"url": url, "headers": kwargs.get("headers"), "params": kwargs.get("params")})
            if url.endswith("/v2/libs/search"):
                return FakeResponse(
                    {
                        "results": [
                            {
                                "id": "/vercel/next.js",
                                "title": "Next.js",
                                "description": "The React Framework",
                            }
                        ]
                    }
                )
            return FakeResponse(
                {
                    "codeSnippets": [
                        {
                            "codeTitle": "Middleware",
                            "codeList": [{"code": "export function middleware() {}"}],
                        }
                    ],
                    "infoSnippets": [{"content": "Ignored because code result fills the limit"}],
                }
            )

    def fake_build_client(*_args, **_kwargs):
        return FakeClient()

    monkeypatch.setattr("app.providers.context7.build_client", fake_build_client)
    results = asyncio.run(provider.search("How to use Next.js middleware", 1))

    assert calls[0]["url"] == "https://context7.com/api/v2/libs/search"
    assert calls[0]["headers"]["Authorization"] == "Bearer ctx"
    assert calls[0]["params"]["query"] == "How to use Next.js middleware"
    assert calls[0]["params"]["fast"] == "true"
    assert calls[1]["url"] == "https://context7.com/api/v2/context"
    assert calls[1]["params"] == {
        "libraryId": "/vercel/next.js",
        "query": "How to use Next.js middleware",
        "type": "json",
    }
    assert len(results) == 1
    assert results[0].title == "Middleware"
    assert results[0].url == "https://context7.com/vercel/next.js"
    assert results[0].snippet == "export function middleware() {}"


def test_context7_provider_explicit_library_id_skips_library_search(monkeypatch):
    provider = Context7Provider(Settings(gateway_api_key="test", context7_api_key="ctx"))
    calls = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return [{"title": "useState", "content": "const [value, setValue] = useState()", "source": "react.dev"}]

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, **kwargs):
            calls.append({"url": url, "params": kwargs.get("params")})
            return FakeResponse()

    def fake_build_client(*_args, **_kwargs):
        return FakeClient()

    monkeypatch.setattr("app.providers.context7.build_client", fake_build_client)
    results = asyncio.run(provider.search("/facebook/react useState docs", 3))

    assert len(calls) == 1
    assert calls[0]["url"] == "https://context7.com/api/v2/context"
    assert calls[0]["params"]["libraryId"] == "/facebook/react"
    assert len(results) == 1
    assert results[0].title == "useState"
    assert results[0].url == "https://context7.com/facebook/react"


def test_context7_provider_follows_library_redirect(monkeypatch):
    provider = Context7Provider(Settings(gateway_api_key="test", context7_api_key="ctx"))
    calls = []

    class FakeResponse:
        def __init__(self, status_code, data):
            self.status_code = status_code
            self.data = data

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise AssertionError(f"unexpected status {self.status_code}")

        def json(self):
            return self.data

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, **kwargs):
            calls.append(kwargs.get("params"))
            if kwargs["params"]["libraryId"] == "/facebook/react":
                return FakeResponse(
                    301,
                    {
                        "error": "library_redirected",
                        "redirectUrl": "/react/react",
                    },
                )
            return FakeResponse(200, {"infoSnippets": [{"content": "React docs"}]})

    def fake_build_client(*_args, **_kwargs):
        return FakeClient()

    monkeypatch.setattr("app.providers.context7.build_client", fake_build_client)
    results = asyncio.run(provider.search("/facebook/react useState docs", 1))

    assert calls[0]["libraryId"] == "/facebook/react"
    assert calls[1]["libraryId"] == "/react/react"
    assert len(results) == 1
    assert results[0].url == "https://context7.com/react/react"
