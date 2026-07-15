from dataclasses import dataclass

from app.config import Settings
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call
from app.utils.url_normalization import extract_canonical_url, normalize_url


@dataclass(frozen=True)
class ExtractedDocument:
    markdown: str
    canonical_url: str = ""
    raw_html: str = ""


class FirecrawlProvider:
    name = "firecrawl"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def extract(self, url: str) -> str:
        return (await self.extract_document(url)).markdown

    async def extract_document(self, url: str) -> ExtractedDocument:
        if not self.settings.firecrawl_api_key:
            raise GatewayError("Firecrawl API Key 未配置", status_code=500)

        async def request() -> ExtractedDocument:
            async with build_client(self.settings) as client:
                resp = await client.post(
                    f"{self.settings.firecrawl_api_url}/scrape",
                    headers={
                        "Authorization": f"Bearer {self.settings.firecrawl_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "url": url,
                        "formats": ["markdown", "rawHtml"],
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
            raw_html = payload.get("rawHtml") or payload.get("raw_html") or ""
            metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
            canonical_url = ""
            for key in ("canonicalURL", "canonicalUrl", "canonical_url", "canonical"):
                value = metadata.get(key) or payload.get(key)
                if isinstance(value, str):
                    canonical_url = normalize_url(value, base_url=url)
                    if canonical_url:
                        break
            if not canonical_url:
                canonical_url = extract_canonical_url(raw_html, url)
            return ExtractedDocument(markdown=markdown, canonical_url=canonical_url, raw_html=raw_html)

        return await timed_call("Firecrawl", request)
