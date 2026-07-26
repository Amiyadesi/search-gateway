import asyncio

from app.config import Settings
from app.routes import extract as extract_route
from app.schemas.extract import ExtractRequest
from app.utils.markdown_quality import assess_markdown_quality


def test_image_proxy_markdown_is_non_text():
    markdown = "\n".join(
        f"![image {index}](https://images.example.net/proxy/{'a' * 180}/{index}.png)"
        for index in range(12)
    )

    quality = assess_markdown_quality(markdown)

    assert quality.status == "non_text"
    assert quality.image_count == 12
    assert quality.readable_chars == 0


def test_navigation_link_pile_is_thin():
    markdown = "\n".join(
        f"- [Documentation section {index}](https://example.com/docs/{index})"
        for index in range(30)
    )

    quality = assess_markdown_quality(markdown)

    assert quality.status == "thin"
    assert quality.link_count == 30
    assert quality.link_density > 0.8


def test_large_link_directory_is_thin_even_with_short_metadata():
    markdown = "\n".join(
        f"- [Example site {index}](https://example{index}.com) - {index * 3} KB"
        for index in range(100)
    )

    quality = assess_markdown_quality(markdown)

    assert quality.status == "thin"
    assert quality.link_count == 100
    assert quality.readable_chars >= 300


def test_normal_article_markdown_is_usable():
    markdown = """
# Reliable web extraction

Web extraction should preserve the main article while removing navigation,
tracking URLs, and image proxy noise. A useful result contains enough readable
prose for a person or model to understand the page without opening every link.

The gateway evaluates readable text, link density, and content structure. It
does not special-case domains. Pages with substantial prose remain usable even
when they contain a few citations or screenshots.

## Verification

Run the same classifier against documentation, blogs, forums, and home pages.
Low-quality extraction should fall back to a search snippet instead of being
presented as complete article content.
"""

    quality = assess_markdown_quality(markdown)

    assert quality.status == "usable"
    assert quality.readable_chars >= 300


def test_extract_route_does_not_report_non_text_markdown_as_success(monkeypatch):
    class FakeProvider:
        def __init__(self, settings):
            self.settings = settings

        async def extract(self, url):
            return "![proxy](https://images.example.net/" + ("x" * 500) + ".png)"

    monkeypatch.setattr(extract_route, "FirecrawlProvider", FakeProvider)

    result = asyncio.run(
        extract_route.extract(
            ExtractRequest(url="https://example.com", screenshot_mode="never"),
            None,
            Settings(gateway_api_key="test"),
        )
    )

    assert result.success is False
    assert result.degraded is True
    assert result.quality == "non_text"
    assert result.readable_chars == 0
    assert result.error == "页面缺少可读正文"


def test_extract_route_keeps_thin_text_as_degraded_content(monkeypatch):
    class FakeProvider:
        def __init__(self, settings):
            self.settings = settings

        async def extract(self, url):
            return "Short but readable article text with a concrete claim and supporting context. " * 2

    monkeypatch.setattr(extract_route, "FirecrawlProvider", FakeProvider)

    result = asyncio.run(
        extract_route.extract(
            ExtractRequest(url="https://example.com", screenshot_mode="never"),
            None,
            Settings(gateway_api_key="test", screenshot_min_markdown_chars=300),
        )
    )

    assert result.success is True
    assert result.degraded is True
    assert result.quality == "thin"
    assert result.error == "页面正文质量不足"
