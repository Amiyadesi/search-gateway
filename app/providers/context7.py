import re
from typing import Any

from app.config import Settings
from app.schemas.common import SearchResult
from app.utils.errors import GatewayError
from app.utils.http import build_client, timed_call


LIBRARY_ID_PATTERN = re.compile(r"(?<!\S)/[A-Za-z0-9_.-]+/[A-Za-z0-9_.@/-]+")


class Context7Provider:
    name = "context7"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        if not self.settings.context7_api_key:
            raise GatewayError("Context7 API Key 未配置", status_code=500)
        if not self.settings.context7_base_url:
            raise GatewayError("Context7 Base URL 未配置", status_code=500)

        async def request() -> list[SearchResult]:
            async with build_client(self.settings, timeout=self.settings.context7_timeout_seconds) as client:
                library = self._library_from_explicit_id(query)
                if library is None:
                    search_resp = await client.get(
                        f"{self.settings.context7_base_url}/v2/libs/search",
                        headers=self._headers(),
                        params={
                            "libraryName": self._library_name(query),
                            "query": query,
                            "fast": str(self.settings.context7_fast_search).lower(),
                        },
                    )
                    search_resp.raise_for_status()
                    library = self._best_library(search_resp.json())

                if not library:
                    return []

                context_resp = await client.get(
                    f"{self.settings.context7_base_url}/v2/context",
                    headers=self._headers(),
                    params={
                        "libraryId": library["id"],
                        "query": query,
                        "type": "json",
                    },
                )
                if getattr(context_resp, "status_code", None) == 301:
                    redirected = self._redirected_library(context_resp.json())
                    if redirected:
                        library["id"] = redirected
                        context_resp = await client.get(
                            f"{self.settings.context7_base_url}/v2/context",
                            headers=self._headers(),
                            params={
                                "libraryId": library["id"],
                                "query": query,
                                "type": "json",
                            },
                        )
                context_resp.raise_for_status()
                results = self._results_from_context(context_resp.json(), library, max_results)
                if results:
                    return results
                return self._results_from_libraries([library], max_results)

        return await timed_call("Context7", request)

    @staticmethod
    def _redirected_library(data: Any) -> str:
        if not isinstance(data, dict):
            return ""
        redirect = data.get("redirectUrl")
        if isinstance(redirect, str) and redirect.startswith("/"):
            return redirect.strip()
        message = data.get("message")
        if isinstance(message, str):
            match = LIBRARY_ID_PATTERN.search(message)
            if match:
                return match.group(0).rstrip(".,;:)")
        return ""

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.context7_api_key}",
            "Accept": "application/json",
        }

    @staticmethod
    def _library_from_explicit_id(query: str) -> dict[str, str] | None:
        match = LIBRARY_ID_PATTERN.search(query)
        if not match:
            return None
        library_id = match.group(0).rstrip(".,;:)")
        return {"id": library_id, "title": library_id.strip("/") or library_id, "description": ""}

    @staticmethod
    def _library_name(query: str) -> str:
        cleaned = re.sub(r"(?i)\b(how|to|use|using|with|for|in|the|a|an|docs?|documentation|api)\b", " ", query)
        cleaned = " ".join(cleaned.split())
        return (cleaned or query).strip()[:500]

    @staticmethod
    def _best_library(data: Any) -> dict[str, Any] | None:
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            return None
        for item in results:
            if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip():
                return item
        return None

    @classmethod
    def _results_from_context(
        cls, data: Any, library: dict[str, Any], max_results: int
    ) -> list[SearchResult]:
        if isinstance(data, list):
            return cls._results_from_documentation_list(data, library, max_results)
        if not isinstance(data, dict):
            return []

        results: list[SearchResult] = []
        results.extend(cls._results_from_code_snippets(data.get("codeSnippets"), library, max_results))
        if len(results) < max_results:
            results.extend(cls._results_from_info_snippets(data.get("infoSnippets"), library, max_results - len(results)))
        if not results:
            docs = data.get("documentation") or data.get("documents") or data.get("results")
            if isinstance(docs, list):
                results = cls._results_from_documentation_list(docs, library, max_results)
        return results[:max_results]

    @classmethod
    def _results_from_documentation_list(
        cls, docs: list[Any], library: dict[str, Any], max_results: int
    ) -> list[SearchResult]:
        results: list[SearchResult] = []
        for item in docs:
            if not isinstance(item, dict):
                continue
            content = cls._first_text(item, "content", "text", "snippet", "description")
            if not content:
                continue
            source = cls._first_text(item, "source", "url")
            results.append(
                SearchResult(
                    title=cls._first_text(item, "title", "codeTitle") or cls._library_title(library),
                    url=source if source.startswith(("http://", "https://")) else cls._library_url(library),
                    snippet=content[:800],
                )
            )
            if len(results) >= max_results:
                break
        return results

    @classmethod
    def _results_from_code_snippets(
        cls, snippets: Any, library: dict[str, Any], max_results: int
    ) -> list[SearchResult]:
        if not isinstance(snippets, list):
            return []

        results: list[SearchResult] = []
        for item in snippets:
            if not isinstance(item, dict):
                continue
            title = cls._first_text(item, "codeTitle", "title") or cls._library_title(library)
            code_list = item.get("codeList")
            if isinstance(code_list, list):
                for code_item in code_list:
                    if not isinstance(code_item, dict):
                        continue
                    code = cls._first_text(code_item, "code", "content", "text")
                    if not code:
                        continue
                    results.append(SearchResult(title=title, url=cls._library_url(library), snippet=code[:800]))
                    if len(results) >= max_results:
                        return results
            else:
                code = cls._first_text(item, "code", "content", "text")
                if code:
                    results.append(SearchResult(title=title, url=cls._library_url(library), snippet=code[:800]))
                    if len(results) >= max_results:
                        return results
        return results

    @classmethod
    def _results_from_info_snippets(
        cls, snippets: Any, library: dict[str, Any], max_results: int
    ) -> list[SearchResult]:
        if not isinstance(snippets, list):
            return []

        results: list[SearchResult] = []
        for item in snippets:
            if not isinstance(item, dict):
                continue
            content = cls._first_text(item, "content", "text", "description")
            if not content:
                continue
            results.append(
                SearchResult(
                    title=cls._first_text(item, "title", "source") or cls._library_title(library),
                    url=cls._library_url(library),
                    snippet=content[:800],
                )
            )
            if len(results) >= max_results:
                break
        return results

    @classmethod
    def _results_from_libraries(cls, libraries: list[dict[str, Any]], max_results: int) -> list[SearchResult]:
        return [
            SearchResult(
                title=cls._first_text(item, "title", "name", "id") or cls._library_title(item),
                url=cls._library_url(item),
                snippet=cls._first_text(item, "description")[:800],
            )
            for item in libraries[:max_results]
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]

    @staticmethod
    def _library_url(library: dict[str, Any]) -> str:
        library_id = str(library.get("id") or "").strip()
        if not library_id:
            return "https://context7.com"
        return f"https://context7.com{library_id if library_id.startswith('/') else '/' + library_id}"

    @staticmethod
    def _library_title(library: dict[str, Any]) -> str:
        title = library.get("title") or library.get("name") or library.get("id") or "Context7 documentation"
        return str(title)

    @staticmethod
    def _first_text(item: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
