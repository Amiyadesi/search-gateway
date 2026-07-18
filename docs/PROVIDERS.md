# Provider setup

Search Gateway ships no third-party credentials. Configure only services whose
terms you accept and whose accounts or deployments you control. Free quotas and
API terms change frequently; the linked provider pages are authoritative.

## Built-in public-data sources

These sources can work without a paid API key, although upstream rate limits
still apply:

| Source | Configuration | Notes |
| --- | --- | --- |
| DuckDuckGo | `DUCKDUCKGO_BASE_URL` | General web fallback |
| Wikipedia / Wikidata | corresponding `*_BASE_URL` | Entity and encyclopedia evidence |
| Hacker News | `HACKERNEWS_BASE_URL` | Public discussion evidence |
| arXiv / OpenAlex / Crossref / PubMed / Semantic Scholar | corresponding `*_BASE_URL` | Academic metadata and discovery |
| Internet Archive / Common Crawl | corresponding settings | Historical and crawl evidence |
| GitHub / Stack Exchange | corresponding base URL and optional credentials | Lower anonymous quotas may apply |

## Free-plan or BYOK services

| Service | Obtain access | Environment variables | Boundary |
| --- | --- | --- | --- |
| [Brave Search API](https://brave.com/search/api/) | Create an account and API subscription | `BRAVE_API_KEY` | Query text is sent to Brave |
| [Tavily](https://tavily.com/) | Create an API key | `TAVILY_API_KEYS` | Supports multiple server-side keys |
| [Exa](https://exa.ai/) | Create an API key | `EXA_API_KEY` | Query text is sent to Exa |
| [Zhihu Global Search](https://developer.zhihu.com/docs?key=global_search) | Apply for an Access Secret | `ZHIHU_API_KEY`, `ZHIHU_TIMEOUT_SECONDS` | Fixed official endpoint; Chinese search evidence only |
| [SerpJet](https://serpjet.io/docs.html) | Create one or two API keys; its documentation currently advertises 1,000 free searches per month | `SERPJET_API_KEYS`, `SERPJET_TIMEOUT_SECONDS` | Final Google-search fallback; keys never reach clients |
| [Context7](https://context7.com/) | Obtain API access | `CONTEXT7_API_KEY`, `CONTEXT7_BASE_URL` | Documentation-focused retrieval |
| Grok-compatible search | Use an authorized compatible endpoint | `GROK_*` | Can use the bundled bridge or configured upstreams |

SerpJet accepts at most two comma-separated keys. The second key is attempted
only after an authentication, quota, rate-limit, timeout, network, or upstream
failure. Each key is sent only in the fixed upstream request's `X-API-KEY`
header. Do not commit either key.

## Self-hosted and OpenAI-compatible services

- SearXNG can provide general metasearch through `SEARXNG_*` settings
- Firecrawl can provide extraction through `FIRECRAWL_*` settings
- The bundled GrokSearch bridge can expose a controlled internal search source
- OpenAI-compatible answer, summary, rerank, and embedding endpoints use their
  corresponding `*_BASE_URL`, `*_MODEL`, and server-side key settings
- Ollama or a privately operated GPT4Free-compatible service can fit the same
  interface when it is exposed through an authorized HTTPS endpoint

GPT4Free is distributed upstream under GPL-3.0. If you deploy or redistribute
it, review that project's license and security model independently; Search
Gateway does not bundle or endorse a public no-key endpoint.

Custom answer endpoints are request-scoped. A bare origin automatically gains
`/v1`; pasted `/models` or `/chat/completions` paths are normalized back to the
API root. Public HTTPS, DNS, redirect, and private-address checks remain active.

## Screenshot providers

The optional screenshot router supports APIFlash, PhantomJSCloud,
ScreenshotMachine, ScreenshotScout, SnapAPI, ScreenshotBase, Thumbnail.ws,
HQAPI, Screenshotlayer, and Microlink. Configure only providers you need and
keep `SCREENSHOT_ALLOW_PRIVATE_URLS=false` unless an isolated internal use case
explicitly requires otherwise.

## Safe configuration workflow

1. Copy `.env.example` to a deployment-local `.env`
2. Set a strong `GATEWAY_API_KEY`
3. Enable the smallest provider set needed for the workload
4. Keep the service bound to localhost, a private network, or an authenticated
   edge policy
5. Verify `/healthz`, then call authenticated `/readyz` and `/health`
6. Run a bounded test query and inspect provenance before enabling automation

The hosted Sayori instance is private infrastructure. This document helps you
operate your own deployment and does not provide credentials or permission to
call that instance.
