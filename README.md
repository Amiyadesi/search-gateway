# Search Gateway 1.2.1

An authenticated FastAPI gateway that gives AI tools one local API for web
search, page extraction, screenshots, research summaries, and optional MCP
access. Providers are configured per deployment, so the repository contains no
runtime credentials or private infrastructure details.

This project acknowledges the [LINUX DO community](https://linux.do/).

## What it does

- `GET /search` routes a query to a configured web, documentation, code,
  encyclopedia, academic, or open-data provider.
- SerpJet can act as the final Google web-search fallback. Up to two server-side
  keys are tried without exposing either key to clients.
- `POST /v1/evidence-search` runs a bounded multi-query/multi-source evidence
  pipeline with URL cleanup, canonical/content deduplication, RRF ranking,
  domain diversity, optional extraction, provenance, budgets, and structured
  partial failures.
- `POST /v1/answer-snapshots` records API-only answer observations through the
  fixed server fallback or a request-scoped OpenAI-compatible API. Custom API
  keys and base URLs are never cached, persisted, logged, or returned.
- `POST /v1/answer-models` lists bounded, sanitized model IDs from a
  request-scoped OpenAI-compatible API.
- `POST /extract` fetches readable Markdown from a public page.
- `POST /screenshot` captures a public page and stores a short-lived,
  authenticated cache entry.
- `POST /summary`, `POST /research`, and `POST /analyze-url` compose the
  available evidence into a bounded result. They return a deterministic
  degraded response when an optional model is unavailable.
- `GET /healthz` is an unauthenticated, dependency-free liveness endpoint.
  Authenticated `GET /readyz` checks only configured internal Redis, SearXNG,
  and GrokSearch bridge dependencies; authenticated `GET /health` shows
  non-secret provider configuration state.
- `mcp/search_gateway_mcp.py` is an optional stdio MCP adapter. It can call a
  gateway over SSH without keeping the gateway API key on the local machine.

The gateway does not ship a search index or third-party credentials. Enable
only the providers you are authorized to use.

Evidence and answer snapshots are dated observations. They do not verify what
ChatGPT, Perplexity, Gemini, Google AI Overview, or any other consumer interface
showed to a user.

## Quick start

Requirements: Docker Compose and a current Python runtime for local tests.

```bash
git clone https://github.com/Amiyadesi/search-gateway.git
cd search-gateway
cp .env.example .env
# Set GATEWAY_API_KEY and the provider settings you intend to use.
docker compose up -d --build
curl http://127.0.0.1:8000/healthz
curl -H "X-API-Key: $GATEWAY_API_KEY" http://127.0.0.1:8000/readyz
```

The default Compose mapping binds the API to `127.0.0.1:8000`. Put a reverse
proxy, tunnel, or firewall policy in front of it if it must be reachable from
another machine.

When the reverse proxy or tunnel runs in Docker, set `EDGE_NETWORK_NAME` to its
shared network and `EDGE_NETWORK_EXTERNAL=true` before recreating the stack.
The API, SearXNG, and the optional compatibility bridge then remain reachable
after `docker compose up --force-recreate`; the default values create an
isolated project-local edge network for ordinary installations.

Authenticated requests use `X-API-Key` or a Bearer token:

```bash
curl -H "X-API-Key: $GATEWAY_API_KEY" \
  "http://127.0.0.1:8000/search?q=FastAPI%20dependency%20injection"
```

## Configuration

Copy `.env.example` and leave unset providers disabled. Its variables are the
authoritative inventory, including optional search, extraction, screenshot,
reranking, embedding, and summary endpoints.

Useful defaults:

- `GATEWAY_API_KEY` protects every endpoint except `/healthz` and the public
  project-introduction page at `/docs`.
- `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and `NO_PROXY` are optional
  deployment-local networking settings. The repository does not prescribe a
  proxy or network name.
- `EDGE_NETWORK_NAME` and `EDGE_NETWORK_EXTERNAL` optionally attach the API and
  networked helpers to an existing Docker network used by a reverse proxy,
  tunnel, or egress proxy.
- `SCREENSHOT_ALLOW_PRIVATE_URLS=false` blocks loopback and private-network
  screenshot targets by default.
- `RERANK_*`, `EMBEDDING_*`, `GROK_*`, and `SUMMARY_*` are optional. A missing
  optional provider must not make ordinary search unavailable.
- `EVIDENCE_*` controls Evidence v1 cache lifetime, provider order, extraction
  concurrency, content limits, and circuit-breaker cooldowns.
- `ZHIHU_API_KEY` is the server-side Access Secret for the optional official
  [Zhihu Global Search API](https://developer.zhihu.com/docs?key=global_search).
  The upstream URL is fixed in source; `ZHIHU_TIMEOUT_SECONDS` only controls
  its request timeout. When it is configured, Chinese `auto` queries can select
  it and Evidence v1 can include it as one of the bounded search sources.
- `SERPJET_API_KEYS` accepts one or two comma-separated server-side keys for the
  optional [SerpJet API](https://serpjet.io/docs.html). SerpJet is last in the
  default ordinary-search and Evidence provider order. The second key is used
  only after auth, credit, rate-limit, timeout, network, or `5xx` failure. Set
  `SERPJET_TIMEOUT_SECONDS` to change its request timeout. Search Gateway sends
  each key only in SerpJet's required `X-API-KEY` header.
- `ANSWER_API_BASE_URL`, `ANSWER_API_MODEL`, and optional `ANSWER_API_KEY`
  configure the fixed OpenAI-compatible fallback. A request can instead submit
  `api_base_url` and `api_model` together with `X-Answer-API-Key`; that custom
  request never uses the server key. An origin-only URL such as
  `https://api.example.com` becomes `https://api.example.com/v1`; pasted
  `/models` or `/chat/completions` suffixes are removed, while custom roots such
  as `/api/v1` remain unchanged.

Do not commit `.env`, Compose overrides, logs, or machine-specific SSH config.

### Supported free, BYOK, and self-hosted services

- No-key or open-data sources include DuckDuckGo Instant Answers, Wikipedia,
  Wikidata, Hacker News, arXiv, OpenAlex, Crossref, PubMed, Semantic Scholar,
  Internet Archive, and Common Crawl. GitHub and Stack Exchange also work with
  lower unauthenticated limits.
- Free-plan or BYOK search sources include Brave Search API, Tavily, Exa,
  SerpJet, Zhihu Global Search, Context7, and optional Grok-compatible entries.
- Self-hosted paths include SearXNG, the bundled GrokSearch bridge, Firecrawl,
  and server-configured OpenAI-compatible answer, summary, rerank, or embedding
  endpoints. Ollama and a privately operated GPT4Free service can fit the
  OpenAI-compatible path when deployed behind an authorized HTTPS endpoint.
- Screenshot fallbacks support APIFlash, PhantomJSCloud, ScreenshotMachine,
  ScreenshotScout, SnapAPI, ScreenshotBase, Thumbnail.ws, HQAPI,
  Screenshotlayer, and Microlink.

See [Provider setup and free/self-hosted options](./docs/PROVIDERS.md) for
official signup links, environment variables, limits, privacy notes, and tested
request examples. Free quotas change; provider pages remain authoritative.

## API surface

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/healthz` | Liveness probe without credentials |
| `GET` | `/readyz` | Authenticated readiness probe for configured internal dependencies |
| `GET` | `/health` | Authenticated provider and cache status |
| `GET` | `/search` | Search with an optional `provider` selector |
| `POST` | `/v1/evidence-search` | Bounded, fused, provenance-rich search evidence |
| `POST` | `/v1/answer-snapshots` | Zero-persistence API answer observations |
| `POST` | `/v1/answer-models` | Sanitized request-scoped model IDs |
| `POST` | `/extract` | Extract readable page content |
| `POST` | `/screenshot` | Capture a public page |
| `GET` | `/screenshot-cache/{id}` | Read a cached screenshot |
| `POST` | `/summary` | Search and produce a bounded summary |
| `POST` | `/research` | Search, extract, and synthesize evidence |
| `POST` | `/analyze-url` | Analyze one public URL |
| `GET` | `/ipinfo` | IP geolocation/intelligence lookup with optional IP.SB fallback |

The public `/docs` route is intentionally an introduction page, not an API
console. Authenticated operators can fetch `/openapi.json` from their own
deployment. The complete versioned contract and error model are in
[docs/API.md](./docs/API.md).

## Agent skill

[`skills/use-search-gateway/SKILL.md`](./skills/use-search-gateway/SKILL.md) is a
copyable Codex skill for connecting an agent to an owned or explicitly
authorized deployment.
It tells the agent to discover credentials locally, preserve provenance, and
avoid treating search evidence as consumer AI visibility. Copy that folder into
your local skills directory; do not point it at the public Sayori deployment.

### Evidence search example

`max_provider_calls` is the maximum number of sources per query and is capped
at two. With three queries, the request can therefore perform at most six
search calls.

```bash
curl -X POST "http://127.0.0.1:8000/v1/evidence-search" \
  -H "X-API-Key: $GATEWAY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "queries": ["evidence based SEO audit"],
    "locale": "en-US",
    "providers": ["auto"],
    "max_results": 8,
    "filters": {"include_domains": [], "exclude_domains": [], "freshness": null},
    "budget": {"max_provider_calls": 2, "max_extract_pages": 5, "timeout_ms": 12000}
  }'
```

### Request-scoped answer API

```bash
curl -X POST "http://127.0.0.1:8000/v1/answer-snapshots" \
  -H "X-API-Key: $GATEWAY_API_KEY" \
  -H "X-Answer-API-Key: $REQUEST_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "queries":["What is evidence-first GEO auditing?"],
    "locale":"en-US",
    "api_base_url":"https://api.example-provider.com",
    "api_model":"example-model"
  }'
```

`api_base_url` and `api_model` must be provided together. The custom URL must
be a credential-free public HTTPS hostname with no explicit port, query, or
fragment. DNS answers are checked before the call, redirects are rejected, and
the server fallback key is never used for a custom endpoint. Copying only the
API origin is enough because `/v1` is appended automatically.

Model selection can use the same request-scoped key without exposing upstream
metadata:

```bash
curl -X POST "http://127.0.0.1:8000/v1/answer-models" \
  -H "X-API-Key: $GATEWAY_API_KEY" \
  -H "X-Answer-API-Key: $REQUEST_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"api_base_url":"https://api.example-provider.com"}'
```

The model response contains at most 100 unique printable IDs of at most 200
characters each. Owner fields, metadata, endpoint details, upstream error
bodies, the key, and the base URL are not returned.

## Optional MCP adapter

The adapter deliberately reads credentials only on the remote host. Configure
the local client with a private SSH host alias and, when needed, a remote
deployment directory:

```text
MCP_SEARCH_GATEWAY_SSH_HOST=your-ssh-alias
MCP_SEARCH_GATEWAY_REMOTE_DIR=/root/search-gateway
```

On the remote machine, `MCP_GATEWAY_ENV_FILE` can point to a deployment-local
`.env`; otherwise the helper reads the `.env` beside its own source. These
values belong in local MCP configuration or server environment, never in this
repository.

The MCP adapter retains all existing tools and adds `ai_evidence_search` and
`ai_answer_snapshot`. The latter uses the server-configured answer credential;
the MCP schema does not accept or transport a user key. `ai_search` and
`ai_evidence_search` accept `zhihu` as an explicit provider only when the
server-side Access Secret is configured; the adapter never receives that
secret.

## Development

```bash
python -B -m pytest tests -q
python -m compileall -q app mcp groksearch-bridge/bridge.py
docker compose config
docker compose build api groksearch-bridge
```

Run the tests before changing routing, provider configuration, or the MCP
adapter. Provider additions should update settings, route validation, health
reporting, documentation, and tests together.

## Image releases

`.github/workflows/release-images.yml` publishes the API and GrokSearch bridge
to GHCR only for a `v*` tag or a manual run from `main`. It never deploys a
production stack and never creates a mutable `latest` tag. Every run publishes
`sha-<git-sha>` tags; tag-triggered runs also publish the matching `v*` tag and
record both content digests in the workflow summary. Production Compose files
should pin the reported digest rather than following a tag.

Base images, third-party service images, and GitHub Actions are pinned. Update
those references deliberately in a reviewed change, then run the complete
test, Compose, and image-build checks before releasing.

## Security and privacy

- Treat all configured API keys and upstream URLs as deployment secrets.
- Keep the API private unless a separate authentication and rate-limit design
  is in place.
- The screenshot service rejects private and loopback targets by default to
  reduce SSRF exposure.
- Evidence extraction validates public HTTP(S) targets before sending them to
  the configured extractor. Search metadata remains usable when extraction is
  blocked or fails.
- Request-scoped answer keys and custom base URLs are never written to Redis,
  files, logs, responses, or analytics by this application. Custom endpoints
  must pass public-HTTPS DNS checks and cannot redirect.
- The optional Zhihu integration sends the query and requested result count to
  Zhihu's fixed Global Search endpoint. Its Access Secret stays in the
  deployment environment; returned attribution URLs are normalized before
  ranking, deduplication, or extraction, while bounded public authorship/edit
  metadata may remain in evidence provenance.
- Search results and model output are external evidence, not a guarantee of
  accuracy. Preserve source URLs and verify high-impact claims independently.

See [docs/PRIVACY.md](./docs/PRIVACY.md) for retention, redaction, BYOK, and
threat-model details.

## License and attribution

Original project code is available under the [MIT License](./LICENSE).
The bundled `groksearch-bridge/GrokSearch` component remains under its own MIT
license. See [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md) for source and
license details.
