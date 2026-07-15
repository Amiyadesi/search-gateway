from typing import Any

from app.config import Settings
from app.schemas.common import SearchResult
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call


class PubMedProvider:
    name = "pubmed"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        if not self.settings.pubmed_base_url:
            raise GatewayError("PubMed Base URL 未配置", status_code=500)

        async def request() -> list[SearchResult]:
            async with build_client(self.settings, timeout=self.settings.pubmed_timeout_seconds) as client:
                search_params = {
                    "db": "pubmed",
                    "term": query,
                    "retmode": "json",
                    "retmax": max_results,
                }
                if self.settings.pubmed_api_key:
                    search_params["api_key"] = self.settings.pubmed_api_key
                search_resp = await client.get(
                    f"{self.settings.pubmed_base_url}/esearch.fcgi",
                    params=search_params,
                    headers={"Accept": "application/json"},
                )
                search_resp.raise_for_status()
                ids = self._ids_from_search(search_resp.json(), max_results)
                if not ids:
                    return []

                summary_params = {
                    "db": "pubmed",
                    "id": ",".join(ids),
                    "retmode": "json",
                }
                if self.settings.pubmed_api_key:
                    summary_params["api_key"] = self.settings.pubmed_api_key
                summary_resp = await client.get(
                    f"{self.settings.pubmed_base_url}/esummary.fcgi",
                    params=summary_params,
                    headers={"Accept": "application/json"},
                )
                summary_resp.raise_for_status()
                summary_data = summary_resp.json()
            return self._results_from_summary(summary_data, ids, max_results)

        return await timed_call("PubMed", request)

    @staticmethod
    def _ids_from_search(data: Any, max_results: int) -> list[str]:
        ids = data.get("esearchresult", {}).get("idlist") if isinstance(data, dict) else None
        if not isinstance(ids, list):
            return []
        return [str(item) for item in ids[:max_results] if str(item).strip()]

    @classmethod
    def _results_from_summary(cls, data: Any, ids: list[str], max_results: int) -> list[SearchResult]:
        result = data.get("result") if isinstance(data, dict) else None
        if not isinstance(result, dict):
            return [cls._id_result(pmid) for pmid in ids[:max_results]]

        results: list[SearchResult] = []
        for pmid in ids:
            item = result.get(pmid)
            if not isinstance(item, dict):
                results.append(cls._id_result(pmid))
                continue
            title = cls._first_text(item, "title") or f"PubMed {pmid}"
            journal = cls._first_text(item, "fulljournalname", "source")
            pubdate = cls._first_text(item, "pubdate")
            authors = item.get("authors")
            author_names = []
            if isinstance(authors, list):
                for author in authors[:3]:
                    if isinstance(author, dict):
                        name = cls._first_text(author, "name")
                        if name:
                            author_names.append(name)
            snippet = " | ".join(part for part in [journal, pubdate, ", ".join(author_names)] if part)[:800]
            results.append(SearchResult(title=title, url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", snippet=snippet))
            if len(results) >= max_results:
                break
        return results

    @staticmethod
    def _id_result(pmid: str) -> SearchResult:
        return SearchResult(title=f"PubMed {pmid}", url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", snippet="")

    @staticmethod
    def _first_text(item: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
