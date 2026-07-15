import xml.etree.ElementTree as ET
from typing import Any

from app.config import Settings
from app.schemas.common import SearchResult
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call


ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


class ArxivProvider:
    name = "arxiv"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        if not self.settings.arxiv_base_url:
            raise GatewayError("arXiv Base URL 未配置", status_code=500)

        async def request() -> list[SearchResult]:
            async with build_client(self.settings, timeout=self.settings.arxiv_timeout_seconds) as client:
                resp = await client.get(
                    self.settings.arxiv_base_url,
                    params={
                        "search_query": f"all:{query}",
                        "start": 0,
                        "max_results": max_results,
                        "sortBy": "relevance",
                    },
                    headers={"Accept": "application/atom+xml"},
                )
                resp.raise_for_status()
            return self._results_from_xml(resp.text, max_results)

        return await timed_call("arXiv", request)

    @classmethod
    def _results_from_xml(cls, text: str, max_results: int) -> list[SearchResult]:
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return []

        results: list[SearchResult] = []
        for entry in root.findall("atom:entry", ATOM_NS):
            title = cls._node_text(entry, "atom:title")
            url = cls._entry_url(entry)
            if not title or not url:
                continue
            summary = cls._node_text(entry, "atom:summary")
            published = cls._node_text(entry, "atom:published")
            snippet = summary
            if published:
                snippet = f"{published[:10]} | {summary}"
            results.append(SearchResult(title=" ".join(title.split()), url=url, snippet=" ".join(snippet.split())[:800]))
            if len(results) >= max_results:
                break
        return results

    @staticmethod
    def _entry_url(entry: ET.Element) -> str:
        for link in entry.findall("atom:link", ATOM_NS):
            if link.attrib.get("rel") == "alternate" and link.attrib.get("href"):
                return link.attrib["href"]
        raw_id = ArxivProvider._node_text(entry, "atom:id")
        return raw_id

    @staticmethod
    def _node_text(entry: ET.Element, path: str) -> str:
        node: Any = entry.find(path, ATOM_NS)
        text = node.text if node is not None else ""
        return text.strip() if isinstance(text, str) else ""
