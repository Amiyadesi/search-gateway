import asyncio

from app.config import Settings
from app.providers.exa import ExaProvider


def test_exa_provider_uses_configured_base_url(monkeypatch):
    provider = ExaProvider(Settings(gateway_api_key="test", exa_api_key="proxy", exa_api_url="https://search.example/exa/"))
    calls: list[str] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"results": []}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **_kwargs):
            calls.append(url)
            return FakeResponse()

    monkeypatch.setattr("app.providers.exa.build_client", lambda *_args, **_kwargs: FakeClient())
    asyncio.run(provider.search("test", 1))
    assert calls == ["https://search.example/exa/search"]
