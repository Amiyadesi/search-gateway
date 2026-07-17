# Search Gateway API 1.2

## Authentication

Every endpoint except `GET /healthz` and `GET /readyz` requires the deployment's
gateway key via `X-API-Key` or `Authorization: Bearer ...`.

`POST /v1/answer-snapshots` accepts `X-Answer-API-Key` only for request-scoped
custom API configuration. `POST /v1/answer-models` always requires it. This
header is not a gateway credential and is never persisted or returned.

## Liveness and readiness

`GET /healthz` is a dependency-free process liveness check and always returns
HTTP 200 while the FastAPI process can serve requests:

```json
{"success": true}
```

`GET /readyz` checks Redis plus SearXNG and the GrokSearch bridge when those
features are enabled. It calls only Redis `PING` and each internal service's
`/healthz`; it never invokes a search or paid external provider. The response
has stable check names and never includes configured URLs, keys, or exception
messages:

```json
{
  "success": true,
  "status": "ready",
  "checks": {
    "redis": {"status": "ok", "required": true, "configured": true},
    "searxng": {"status": "disabled", "required": false, "configured": false},
    "groksearch_bridge": {"status": "disabled", "required": false, "configured": false}
  }
}
```

Required checks use `ok`, `misconfigured`, or `unavailable`; disabled optional
checks use `disabled`. HTTP 200 means all required checks are `ok`. Any required
failure returns HTTP 503 with `success: false` and `status: not_ready`.

## Evidence v1

### `POST /v1/evidence-search`

```json
{
  "queries": ["query one"],
  "locale": "en-US",
  "providers": ["auto"],
  "max_results": 8,
  "filters": {
    "include_domains": [],
    "exclude_domains": [],
    "freshness": null
  },
  "budget": {
    "max_provider_calls": 2,
    "max_extract_pages": 5,
    "timeout_ms": 12000
  },
  "rerank": true
}
```

Limits are enforced by the request schema:

- one to three unique queries, each at most 500 characters;
- one or two explicit providers, or `auto` by itself;
- one or two provider calls per query;
- zero to five extracted pages;
- one to ten final results;
- a total deadline from 1,000 to 30,000 milliseconds.

The response contains:

- `evidence_version`, request timestamps, and the normalized `query_plan`;
- fused `results` with stable `source_id`, every query/provider/rank origin,
  canonical URL, registrable domain, RRF score, extraction state, and optional
  content hash;
- one `provider_runs` entry for every attempted or circuit-blocked source;
- actual usage, cache hits, `partial`, `degraded`, and sanitized `errors`;
- limitations that prevent search evidence from being mistaken for consumer AI
  citation monitoring.

Rank fusion uses `sum(1 / (60 + rank))`. Provider-native scores are not mixed.
After URL and canonical deduplication, no registrable domain contributes more
than two final results by default. An optional reranker operates on the fused
candidate set; failure retains deterministic RRF order.

Tracking parameters and fragments are removed before deduplication. Extraction
can update identity from a declared canonical URL and exact normalized content
hash. Near-duplicate semantic clustering is not part of Evidence v1.

Each `origin` may contain bounded `provider_metadata`. This field is provenance,
not a ranking input. For the official Zhihu source it can include content type
and ID, author name/badge text, edit time, engagement counts, authority level,
and the observed attribution URL. The result's `url` and `canonical_url` remain
normalized and are the only URL identities used for deduplication, diversity,
RRF, extraction, and source IDs.

### Optional Zhihu Global Search source

Set `ZHIHU_API_KEY` to the deployment's Zhihu Access Secret and optionally set
`ZHIHU_TIMEOUT_SECONDS` (default `15`). Search Gateway always calls the fixed
official endpoint:

`GET https://developer.zhihu.com/api/v1/content/global_search`

It sends `Authorization: Bearer ...`, a seconds-level `X-Request-Timestamp`,
`Content-Type: application/json`, `Query`, bounded `Count`, and `SearchDB=all`.
Clients cannot submit a Zhihu endpoint or credential. `GET /search` accepts
`provider=zhihu`; Chinese `provider=auto` queries prefer it when configured.
Evidence v1 can use `zhihu` explicitly or select it for Chinese queries while
still enforcing the two-provider-per-query budget.

Successful items map `Title`, cleaned `ContentText`, and a tracking-normalized
`Url` into the shared search result. Auth (`401/403`), quota (`429`), timeout,
network, malformed-response, and `5xx` failures enter the same sanitized
provider-run states and cooldown rules as other Evidence v1 sources. Zhihu
results are search evidence only and never imply consumer AI citation or
visibility.

### Provider and extraction states

Provider runs use:

`complete | empty | timeout | auth_error | rate_limited | upstream_error | invalid_request | unavailable | circuit_open`

Extraction uses:

`complete | not_requested | blocked | timeout | error`

One successful source plus one failure returns HTTP 200 with useful evidence,
`partial: true`, and `degraded: true`. If all attempted sources fail, the API
returns the same structured envelope with HTTP 502 for non-retryable failures
or HTTP 503 when retryable failures are present. A successful empty search is
not treated as an upstream failure.

Authentication failures open a long circuit, `429` respects `Retry-After`, and
timeouts/network/5xx failures open shorter circuits. Redis failure is fail-open:
it can reduce cache/circuit protection but does not erase successful evidence.

Freshness participates in validation, provenance, and cache identity. Exact
freshness enforcement remains dependent on the selected source's capabilities
and is disclosed in `limitations`.

## Answer snapshot v1

### `POST /v1/answer-snapshots`

```json
{
  "queries": ["question one"],
  "locale": "en-US",
  "api_base_url": "https://api.example-provider.com",
  "api_model": "example-model"
}
```

`api_base_url` and `api_model` are optional but must be supplied together. When
they are absent, the service uses only the fixed server endpoint, model, and
key. When they are present, `X-Answer-API-Key` is required and the server key is
never used.

Custom endpoints must use a credential-free public HTTPS hostname. IP literals,
explicit ports, localhost/reserved names, private or reserved DNS answers,
queries, fragments, and redirects are rejected. A trailing `/v1`,
`/chat/completions`, or `/models` is normalized before the fixed endpoint path
is appended. An origin-only URL automatically receives `/v1`; existing roots
such as `/api/v1` remain unchanged. Pasting a full `/models` or
`/chat/completions` endpoint backs up to its API root first.

The response contains `snapshot_version`, observation time, configured API/model
metadata, per-query answer text, citations returned by that API, timing, numeric
usage, statuses, limitations, and sanitized errors. It explicitly reports
`zero_persistence: true`.

`ANSWER_API_TIMEOUT_SECONDS` remains `30` by default. It is an HTTP transport
phase timeout, not a claim about model-generation duration. Timeout errors retain
the stable `ANSWER_API_TIMEOUT` code and include `timeout_phase` as `connect`,
`write`, `read`, `pool`, `upstream`, `gateway`, or `unknown`. Trace data maps a
response-header wait to `upstream` and a response-body timeout to `read`. A custom
transport that emits no trace events falls back to the underlying `httpx`
exception class.

Each observation can include a sanitized `timing` object. `connect_ms` sums TCP
or Unix-socket connection time and TLS setup; it is `null` when a reused
connection emits no connection event. `request_write_ms` sums request header and
body writes. `upstream_wait_ms` measures from the completed request write through
complete response headers. It includes network latency, upstream queueing, and
generation, so `upstream_wait_is_approximation` is always `true` and the value is
not exact model-generation time. `response_read_ms` measures response-body reads,
and `total_ms` is the complete gateway-side observation time. These fields are
not mutually exclusive, and their sum can be lower than `total_ms` because total
time also includes parsing and framework overhead.

Timing uses the supported `httpcore` trace extension. The callback accepts only
recognized event names and monotonic timestamps. It discards trace metadata and
never stores or returns request URLs, headers, keys, bodies, response content, or
exception text. Failed requests return any phases observed before the failure.

This endpoint records API observations only. It does not claim equivalence to a
provider's website, app, personalized account, region, or consumer search mode.

### `POST /v1/answer-models`

```json
{
  "api_base_url": "https://api.example-provider.com"
}
```

This authenticated route also requires `X-Answer-API-Key`. It calls only the
normalized custom base URL's `/models` endpoint with redirects disabled. A
successful response has this shape:

```json
{
  "success": true,
  "models": ["model-a", "model-b"]
}
```

Only model IDs are retained. IDs are trimmed, de-duplicated in upstream order,
limited to 100 entries and 200 printable characters each, and entries with
control characters are dropped. Owner, metadata, endpoint, and other upstream
fields are discarded.

Custom answer API failures use the normal gateway envelope with stable codes:
`ANSWER_API_URL_INVALID`, `ANSWER_API_KEY_REQUIRED`,
`ANSWER_API_REDIRECT_BLOCKED`, `ANSWER_API_AUTH_ERROR`,
`ANSWER_API_RATE_LIMITED`, `ANSWER_API_TIMEOUT`,
`ANSWER_API_NETWORK_ERROR`, `ANSWER_API_UPSTREAM_ERROR`,
`ANSWER_API_INVALID_REQUEST`, or `ANSWER_API_MALFORMED_RESPONSE`. Error
responses never include the key, custom base URL, redirect location, or raw
upstream body.

## MCP

The stdio adapter exposes all legacy tools plus:

- `ai_evidence_search`, matching the Evidence v1 limits and request fields;
- `ai_answer_snapshot`, using the server-configured answer credential.

`ai_search` and `ai_evidence_search` include `zhihu` in their provider enum.
They call Search Gateway's normalized provider implementation rather than
exposing the upstream Access Secret or connecting a client directly to Zhihu.

The MCP answer tool intentionally has no key, endpoint, or model parameter.

## Compatibility

`GET /search` and all pre-Evidence MCP tools preserve their existing request and
response contracts. Evidence v1 uses new versioned routes and independent cache
keys.
