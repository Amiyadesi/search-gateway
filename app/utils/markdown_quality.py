import html
import re
from dataclasses import dataclass
from typing import Literal


MarkdownQualityStatus = Literal["usable", "thin", "non_text"]

_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^\n)]*\)")
_REFERENCE_IMAGE_RE = re.compile(r"!\[[^\]]*\]\[[^\]]*\]")
_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\([^\n)]*\)")
_REFERENCE_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\[[^\]]*\]")
_AUTOLINK_RE = re.compile(r"<https?://[^>]+>", re.IGNORECASE)
_BARE_URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MARKDOWN_MARKER_RE = re.compile(r"(?:^|\s)[#>*_~`|=-]+(?=\s|$)", re.MULTILINE)
_READABLE_CHAR_RE = re.compile(r"[A-Za-z0-9\u3400-\u9fff]")


@dataclass(frozen=True)
class MarkdownQuality:
    status: MarkdownQualityStatus
    readable_chars: int
    link_count: int
    image_count: int
    paragraph_count: int
    link_density: float


def assess_markdown_quality(markdown: str, min_readable_chars: int = 300) -> MarkdownQuality:
    """Estimate whether extracted Markdown contains prose instead of URL or navigation noise."""
    source = markdown or ""
    image_count = len(_IMAGE_RE.findall(source)) + len(_REFERENCE_IMAGE_RE.findall(source))
    without_images = _REFERENCE_IMAGE_RE.sub(" ", _IMAGE_RE.sub(" ", source))

    link_labels: list[str] = []

    def replace_link(match: re.Match[str]) -> str:
        label = match.group(1)
        link_labels.append(label)
        return f" {label} "

    text = _LINK_RE.sub(replace_link, without_images)
    text = _REFERENCE_LINK_RE.sub(replace_link, text)
    link_count = len(link_labels)
    text = _AUTOLINK_RE.sub(" ", text)
    text = _BARE_URL_RE.sub(" ", text)
    text = _HTML_TAG_RE.sub(" ", text)
    text = _MARKDOWN_MARKER_RE.sub(" ", html.unescape(text))

    readable_chars = len(_READABLE_CHAR_RE.findall(text))
    link_chars = sum(len(_READABLE_CHAR_RE.findall(label)) for label in link_labels)
    link_density = min(1.0, link_chars / readable_chars) if readable_chars else 0.0
    paragraph_count = sum(
        1
        for block in re.split(r"\n\s*\n", text)
        if len(_READABLE_CHAR_RE.findall(block)) >= 40
    )

    minimum = max(1, min_readable_chars)
    non_text_limit = max(1, min(100, minimum // 3))
    non_link_chars = max(0, readable_chars - link_chars)
    if readable_chars < non_text_limit:
        status: MarkdownQualityStatus = "non_text"
    elif readable_chars < minimum:
        status = "thin"
    elif link_count >= 50 and link_density >= 0.55:
        status = "thin"
    elif link_count >= 8 and link_density >= 0.65 and non_link_chars < minimum:
        status = "thin"
    else:
        status = "usable"

    return MarkdownQuality(
        status=status,
        readable_chars=readable_chars,
        link_count=link_count,
        image_count=image_count,
        paragraph_count=paragraph_count,
        link_density=round(link_density, 4),
    )
