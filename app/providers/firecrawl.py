from app.config import Settings
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call


class FirecrawlProvider:
    name = "firecrawl"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def extract(self, url: str) -> str:
        if not self.settings.firecrawl_api_key:
            raise GatewayError("Firecrawl API Key 未配置", status_code=500)

        async def request() -> str:
            async with build_client(self.settings) as client:
                resp = await client.post(
                    "https://api.firecrawl.dev/v2/scrape",
                    headers={
                        "Authorization": f"Bearer {self.settings.firecrawl_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "url": url,
                        "formats": ["markdown"],
                        "onlyMainContent": True,
                        "blockAds": True,
                        "removeBase64Images": True,
                        "timeout": int(self.settings.request_timeout_seconds * 1000),
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            payload = data.get("data") or data
            markdown = payload.get("markdown") or ""
            if not markdown:
                raise GatewayError("Firecrawl 未返回 markdown", status_code=502)
            return markdown

        return await timed_call("Firecrawl", request)
