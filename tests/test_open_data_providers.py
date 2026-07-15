import asyncio

from app.config import Settings
from app.providers.arxiv import ArxivProvider
from app.providers.common_crawl import CommonCrawlProvider
from app.providers.crossref import CrossrefProvider
from app.providers.hackernews import HackerNewsProvider
from app.providers.internet_archive import InternetArchiveProvider
from app.providers.openalex import OpenAlexProvider
from app.providers.pubmed import PubMedProvider
from app.providers.semantic_scholar import SemanticScholarProvider
from app.providers.wikidata import WikidataProvider
from app.providers.wikipedia import WikipediaProvider


class FakeResponse:
    def __init__(self, data=None, text=""):
        self.data = data
        self.text = text

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.data


def test_wikipedia_provider_parses_search_results(monkeypatch):
    provider = WikipediaProvider(Settings(gateway_api_key="test", open_data_user_agent="test-agent"))
    calls = {"url": "", "params": {}, "headers": {}}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, **kwargs):
            calls["url"] = url
            calls["params"] = kwargs.get("params") or {}
            calls["headers"] = kwargs.get("headers") or {}
            return FakeResponse(
                {"query": {"search": [{"title": "Python (programming language)", "snippet": "<span>Python</span>"}]}}
            )

    monkeypatch.setattr("app.providers.wikipedia.build_client", lambda *_args, **_kwargs: FakeClient())
    results = asyncio.run(provider.search("python", 1))

    assert calls["url"] == "https://en.wikipedia.org/w/api.php"
    assert calls["params"]["srsearch"] == "python"
    assert calls["headers"]["User-Agent"] == "test-agent"
    assert results[0].title == "Python (programming language)"
    assert results[0].url == "https://en.wikipedia.org/wiki/Python_(programming_language)"
    assert results[0].snippet == "Python"


def test_wikidata_provider_parses_entity_results(monkeypatch):
    provider = WikidataProvider(Settings(gateway_api_key="test"))

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *_args, **_kwargs):
            return FakeResponse(
                {
                    "search": [
                        {
                            "id": "Q28865",
                            "label": "Python",
                            "description": "programming language",
                            "concepturi": "https://www.wikidata.org/wiki/Q28865",
                        }
                    ]
                }
            )

    monkeypatch.setattr("app.providers.wikidata.build_client", lambda *_args, **_kwargs: FakeClient())
    results = asyncio.run(provider.search("python", 1))

    assert results[0].title == "Python"
    assert results[0].url == "https://www.wikidata.org/wiki/Q28865"
    assert "programming language" in results[0].snippet


def test_hackernews_provider_parses_algolia_results(monkeypatch):
    provider = HackerNewsProvider(Settings(gateway_api_key="test"))
    calls = {"url": "", "params": {}}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, **kwargs):
            calls["url"] = url
            calls["params"] = kwargs.get("params") or {}
            return FakeResponse({"hits": [{"title": "Launch HN", "url": "https://example.com", "points": 42}]})

    monkeypatch.setattr("app.providers.hackernews.build_client", lambda *_args, **_kwargs: FakeClient())
    results = asyncio.run(provider.search("openai", 1))

    assert calls["url"] == "https://hn.algolia.com/api/v1/search"
    assert calls["params"]["tags"] == "story"
    assert results[0].title == "Launch HN"
    assert "42 points" in results[0].snippet


def test_arxiv_provider_parses_atom_results(monkeypatch):
    provider = ArxivProvider(Settings(gateway_api_key="test"))
    atom = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>http://arxiv.org/abs/1706.03762v7</id>
        <title>Attention Is All You Need</title>
        <summary>Transformer architecture.</summary>
        <published>2017-06-12T00:00:00Z</published>
        <link href="http://arxiv.org/abs/1706.03762v7" rel="alternate" type="text/html"/>
      </entry>
    </feed>"""

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *_args, **_kwargs):
            return FakeResponse(text=atom)

    monkeypatch.setattr("app.providers.arxiv.build_client", lambda *_args, **_kwargs: FakeClient())
    results = asyncio.run(provider.search("transformer", 1))

    assert results[0].title == "Attention Is All You Need"
    assert results[0].url == "http://arxiv.org/abs/1706.03762v7"
    assert "2017-06-12" in results[0].snippet


def test_openalex_provider_parses_work_results(monkeypatch):
    provider = OpenAlexProvider(Settings(gateway_api_key="test", open_data_contact_email="me@example.com"))
    calls = {"params": {}}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *_args, **kwargs):
            calls["params"] = kwargs.get("params") or {}
            return FakeResponse(
                {
                    "results": [
                        {
                            "display_name": "A paper",
                            "publication_year": 2024,
                            "primary_location": {"landing_page_url": "https://example.org/paper"},
                            "abstract_inverted_index": {"hello": [0], "world": [1]},
                        }
                    ]
                }
            )

    monkeypatch.setattr("app.providers.openalex.build_client", lambda *_args, **_kwargs: FakeClient())
    results = asyncio.run(provider.search("paper", 1))

    assert calls["params"]["mailto"] == "me@example.com"
    assert results[0].title == "A paper"
    assert results[0].url == "https://example.org/paper"
    assert "hello world" in results[0].snippet


def test_crossref_provider_parses_work_results(monkeypatch):
    provider = CrossrefProvider(Settings(gateway_api_key="test"))

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *_args, **_kwargs):
            return FakeResponse(
                {
                    "message": {
                        "items": [
                            {
                                "title": ["A Crossref Paper"],
                                "URL": "https://doi.org/10.1000/test",
                                "container-title": ["Journal"],
                                "published-online": {"date-parts": [[2023, 1, 2]]},
                                "abstract": "<jats:p>Abstract text.</jats:p>",
                            }
                        ]
                    }
                }
            )

    monkeypatch.setattr("app.providers.crossref.build_client", lambda *_args, **_kwargs: FakeClient())
    results = asyncio.run(provider.search("machine learning", 1))

    assert results[0].title == "A Crossref Paper"
    assert results[0].url == "https://doi.org/10.1000/test"
    assert "Journal" in results[0].snippet
    assert "Abstract text." in results[0].snippet


def test_pubmed_provider_searches_then_summarizes(monkeypatch):
    provider = PubMedProvider(Settings(gateway_api_key="test", pubmed_api_key="pm-key"))
    calls = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, **kwargs):
            calls.append({"url": url, "params": kwargs.get("params") or {}})
            if url.endswith("/esearch.fcgi"):
                return FakeResponse({"esearchresult": {"idlist": ["123"]}})
            return FakeResponse(
                {
                    "result": {
                        "123": {
                            "title": "Cancer study",
                            "fulljournalname": "Medical Journal",
                            "pubdate": "2024",
                            "authors": [{"name": "Ada Lovelace"}],
                        }
                    }
                }
            )

    monkeypatch.setattr("app.providers.pubmed.build_client", lambda *_args, **_kwargs: FakeClient())
    results = asyncio.run(provider.search("cancer", 1))

    assert calls[0]["url"].endswith("/esearch.fcgi")
    assert calls[0]["params"]["api_key"] == "pm-key"
    assert calls[1]["url"].endswith("/esummary.fcgi")
    assert results[0].title == "Cancer study"
    assert results[0].url == "https://pubmed.ncbi.nlm.nih.gov/123/"


def test_semantic_scholar_provider_parses_papers(monkeypatch):
    provider = SemanticScholarProvider(Settings(gateway_api_key="test", semantic_scholar_api_key="ss-key"))
    calls = {"headers": {}}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *_args, **kwargs):
            calls["headers"] = kwargs.get("headers") or {}
            return FakeResponse(
                {
                    "data": [
                        {
                            "title": "Transformer paper",
                            "url": "https://semanticscholar.org/paper/1",
                            "year": 2017,
                            "venue": "NeurIPS",
                            "authors": [{"name": "A. Author"}],
                            "abstract": "Attention.",
                        }
                    ]
                }
            )

    monkeypatch.setattr("app.providers.semantic_scholar.build_client", lambda *_args, **_kwargs: FakeClient())
    results = asyncio.run(provider.search("transformer", 1))

    assert calls["headers"]["x-api-key"] == "ss-key"
    assert results[0].title == "Transformer paper"
    assert "NeurIPS" in results[0].snippet


def test_internet_archive_provider_parses_docs(monkeypatch):
    provider = InternetArchiveProvider(Settings(gateway_api_key="test"))

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *_args, **_kwargs):
            return FakeResponse(
                {"response": {"docs": [{"identifier": "python_docs", "title": "Python Docs", "description": "Manual"}]}}
            )

    monkeypatch.setattr("app.providers.internet_archive.build_client", lambda *_args, **_kwargs: FakeClient())
    results = asyncio.run(provider.search("python", 1))

    assert results[0].title == "Python Docs"
    assert results[0].url == "https://archive.org/details/python_docs"


def test_common_crawl_provider_discovers_latest_index_and_parses_lines(monkeypatch):
    provider = CommonCrawlProvider(Settings(gateway_api_key="test"))
    calls = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, **kwargs):
            calls.append({"url": url, "params": kwargs.get("params") or {}})
            if url.endswith("/collinfo.json"):
                return FakeResponse([{"cdx-api": "https://index.commoncrawl.org/CC-MAIN-test-index"}])
            return FakeResponse(
                text='{"url":"https://example.com/","timestamp":"20240101000000","status":"200","mime":"text/html"}\n'
            )

    monkeypatch.setattr("app.providers.common_crawl.build_client", lambda *_args, **_kwargs: FakeClient())
    results = asyncio.run(provider.search("example.com", 1))

    assert calls[0]["url"] == "https://index.commoncrawl.org/collinfo.json"
    assert calls[1]["url"] == "https://index.commoncrawl.org/CC-MAIN-test-index"
    assert calls[1]["params"]["url"] == "example.com/*"
    assert results[0].url == "https://example.com/"
    assert "timestamp 20240101000000" in results[0].snippet
