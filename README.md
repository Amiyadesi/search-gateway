# Search Gateway

An authenticated FastAPI gateway that gives AI tools one local API for web
search, page extraction, screenshots, research summaries, and optional MCP
access. Providers are configured per deployment, so the repository contains no
runtime credentials or private infrastructure details.

This project acknowledges the [LINUX DO community](https://linux.do/).

## What it does

- `GET /search` routes a query to a configured web, documentation, code,
  encyclopedia, academic, or open-data provider.
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
- `GET /healthz` is an unauthenticated liveness endpoint; `GET /health` shows
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
```

The default Compose mapping binds the API to `127.0.0.1:8000`. Put a reverse
proxy, tunnel, or firewall policy in front of it if it must be reachable from
another machine.

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

- `GATEWAY_API_KEY` protects every endpoint except `/healthz`.
- `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and `NO_PROXY` are optional
  deployment-local networking settings. The repository does not prescribe a
  proxy or network name.
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
- `ANSWER_API_BASE_URL`, `ANSWER_API_MODEL`, and optional `ANSWER_API_KEY`
  configure the fixed OpenAI-compatible fallback. A request can instead submit
  `api_base_url` and `api_model` together with `X-Answer-API-Key`; that custom
  request never uses the server key.

Do not commit `.env`, Compose overrides, logs, or machine-specific SSH config.

## API surface

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/healthz` | Liveness probe without credentials |
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
| `GET` | `/ipinfo` | Optional IP intelligence lookup |

See the FastAPI OpenAPI page from a running local service for request and
response schemas. The complete versioned contract and error model are in
[docs/API.md](./docs/API.md).

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
    "api_base_url":"https://api.example-provider.com/v1",
    "api_model":"example-model"
  }'
```

`api_base_url` and `api_model` must be provided together. The custom URL must
be a credential-free public HTTPS hostname with no explicit port, query, or
fragment. DNS answers are checked before the call, redirects are rejected, and
the server fallback key is never used for a custom endpoint.

Model selection can use the same request-scoped key without exposing upstream
metadata:

```bash
curl -X POST "http://127.0.0.1:8000/v1/answer-models" \
  -H "X-API-Key: $GATEWAY_API_KEY" \
  -H "X-Answer-API-Key: $REQUEST_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"api_base_url":"https://api.example-provider.com/v1"}'
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
python -m compileall -q app mcp
docker compose config
```

Run the tests before changing routing, provider configuration, or the MCP
adapter. Provider additions should update settings, route validation, health
reporting, documentation, and tests together.

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
