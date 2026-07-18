# Privacy and Security Model

## Data classes

Search Gateway handles four relevant data classes:

1. gateway and upstream credentials supplied through deployment-local settings;
2. request-scoped answer API keys, base URLs, and model IDs supplied for custom
   OpenAI-compatible calls;
3. search queries, public result metadata, and optionally extracted public page
   content;
4. API answer text, citations, timing, and numeric usage returned to the caller.

Optional search credentials such as `ZHIHU_API_KEY` are server-owned provider
secrets, not BYOK values. They are read from deployment-local settings and are
never returned through search results, Evidence provenance, health output, or
MCP responses.

Optional SerpJet credentials in `SERPJET_API_KEYS` follow the same boundary.
Search Gateway sends them only to the fixed endpoint documented at
https://serpjet.io/docs.html through `X-API-KEY`. SerpJet states that it logs API
call time, API key, search keywords, result type, and response status, retaining
API call logs for 12 months. Operators should evaluate that upstream retention
before enabling the provider.

The repository contains configuration names and empty examples only. Runtime
credentials must remain outside Git.

## Zero-persistence BYOK boundary

The request-scoped answer key exists only as a local variable while the request
is executing. Application code does not place it in:

- request or response JSON;
- Redis search/evidence/screenshot caches;
- files, databases, analytics, task artifacts, or exported reports;
- application log messages or upstream error bodies.

For a fixed answer request, only the deployment's configured endpoint, model,
and key are used. For a custom answer request, the key is sent only as a Bearer
credential to the validated request-scoped base URL, and the deployment key is
never used. The gateway neither retries through another answer provider nor
forwards the key to search, extraction, reranking, or summary services.

Custom base URLs and model IDs are not cached or returned. The model-list route
returns only cleaned model IDs and discards owner, metadata, endpoint, and all
other upstream fields.

Reverse proxies, infrastructure tracing, and hosting platforms are separate
trust boundaries. Operators must keep header/body logging disabled or redacted
for credential-bearing requests.

## Retention

- Ordinary provider search results use the existing Redis cache lifetime.
- Complete, non-degraded Evidence v1 envelopes use
  `EVIDENCE_CACHE_TTL_SECONDS`; the cache key contains the algorithm version,
  normalized queries, locale, provider set, filters, extraction budget, and
  rerank configuration.
- Provider circuit state is short-lived Redis data with failure-specific TTLs.
- Answer snapshots and request-scoped keys are not cached or stored by Search
  Gateway. Persistence, if a caller explicitly chooses it, belongs to that
  caller's separate data model and retention policy.
- Redis is configured as an in-memory cache in the supplied Compose file.

## Error redaction

Public errors expose stable codes, stage, retryability, and bounded cooldowns.
They do not return request headers, credentials, full upstream bodies, or local
endpoint configuration. Application logging records provider identifiers,
status categories, and timing, not query bodies or credential values.

## SSRF and network boundaries

- A client-selected answer API must be a credential-free public HTTPS hostname
  with no explicit port, query, or fragment. IP literals and reserved hostnames
  are rejected. DNS resolution runs off the async event loop and every resolved
  address must be globally routable. HTTP redirects are never followed and any
  `3xx` response is rejected without exposing its location.
- Evidence extraction accepts only normalized, credential-free HTTP(S) URLs and
  rejects loopback, private, link-local, multicast, reserved, and unresolvable
  targets before invoking the extractor.
- Tracking parameters and fragments are removed before extraction identity and
  caching decisions.
- The official Zhihu Global Search integration sends only the normalized query,
  bounded result count, fixed `SearchDB=all`, authentication headers, and the
  required seconds-level timestamp to its fixed endpoint. Clients cannot
  redirect that credential to another URL.
- Zhihu's public authorship, edit-time, engagement, authority, and observed URL
  fields may be retained as bounded provider provenance. The normalized URL,
  not the attribution URL, controls deduplication, source IDs, ranking, and
  extraction.
- Search metadata is still returned when extraction is blocked, times out, or
  fails.
- Screenshot requests retain their independent public-target validation.
- The supplied Compose file binds the API to loopback. A public deployment must
  add its own authenticated reverse proxy, firewall, rate limit, and TLS policy.

The configured external extractor is another trust boundary and controls its
own redirect behavior. Operators should select an extractor that revalidates
redirect targets and should not enable private-network access.

## Threat model and limitations

Mitigated in the application:

- direct endpoint/key exfiltration to HTTP, literal-IP, local, private,
  reserved, explicitly ported, or redirect-selected answer targets;
- cache or response leakage of request-scoped keys;
- duplicate/tracking URLs inflating evidence;
- a failing source being reported as a zero-result success;
- unbounded source fan-out, page extraction, or response content;
- raw upstream errors leaking credential-bearing response text.
- client-controlled endpoint substitution for the Zhihu credential.

Outside this repository's guarantees:

- reverse-proxy or host-level header logging;
- upstream provider retention and terms;
- malicious or inaccurate public page content;
- DNS rebinding after the pre-request resolution check and host/proxy-level
  routing outside the application process;
- DNS rebinding or redirect behavior inside an external extraction service;
- accuracy, completeness, personalization, or future stability of search and
  answer observations.

Evidence and answer snapshots must be presented as dated observations. They are
not proof of visibility or citation in a consumer AI interface.
