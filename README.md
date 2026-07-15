# Search Gateway

An authenticated FastAPI gateway that gives AI tools one local API for web
search, page extraction, screenshots, research summaries, and optional MCP
access. Providers are configured per deployment, so the repository contains no
runtime credentials or private infrastructure details.

This project acknowledges the [LINUX DO community](https://linux.do/).

## What it does

- `GET /search` routes a query to a configured web, documentation, code,
  encyclopedia, academic, or open-data provider.
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

Do not commit `.env`, Compose overrides, logs, or machine-specific SSH config.

## API surface

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/healthz` | Liveness probe without credentials |
| `GET` | `/health` | Authenticated provider and cache status |
| `GET` | `/search` | Search with an optional `provider` selector |
| `POST` | `/extract` | Extract readable page content |
| `POST` | `/screenshot` | Capture a public page |
| `GET` | `/screenshot-cache/{id}` | Read a cached screenshot |
| `POST` | `/summary` | Search and produce a bounded summary |
| `POST` | `/research` | Search, extract, and synthesize evidence |
| `POST` | `/analyze-url` | Analyze one public URL |
| `GET` | `/ipinfo` | Optional IP intelligence lookup |

See the FastAPI OpenAPI page from a running local service for request and
response schemas.

## Optional MCP adapter

The adapter deliberately reads credentials only on the remote host. Configure
the local client with a private SSH host alias and, when needed, a remote
deployment directory:

```text
MCP_SEARCH_GATEWAY_SSH_HOST=your-ssh-alias
MCP_SEARCH_GATEWAY_REMOTE_DIR=/opt/search-gateway
```

On the remote machine, `MCP_GATEWAY_ENV_FILE` can point to a deployment-local
`.env`; otherwise the helper reads the `.env` beside its own source. These
values belong in local MCP configuration or server environment, never in this
repository.

## Development

```bash
python -B -m pytest tests -q
python -m py_compile app/config.py mcp/search_gateway_mcp.py
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
- Search results and model output are external evidence, not a guarantee of
  accuracy. Preserve source URLs and verify high-impact claims independently.

## License and attribution

Original project code is available under the [MIT License](./LICENSE).
The bundled `groksearch-bridge/GrokSearch` component remains under its own MIT
license. See [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md) for source and
license details.
