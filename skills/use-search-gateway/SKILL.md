---
name: use-search-gateway
description: Connect an AI agent to an owned or explicitly authorized Search Gateway deployment for web search, evidence collection, extraction, and dated answer snapshots. Use when a task needs sourced external research through Search Gateway, needs to verify provenance or provider failures, or needs to configure the local MCP adapter without exposing gateway or upstream credentials.
---

# Use Search Gateway

Use only a deployment the user owns or has explicitly authorized. Treat the
public Sayori instance as private infrastructure unless the user supplies valid
local credentials and states that it is in scope.

## Connect

1. Inspect project-local configuration and environment variables before asking
   for values already available locally
2. Expect a gateway base URL and gateway key; never print the key
3. Prefer the repository's stdio MCP adapter when it is already configured
4. Otherwise call the owned deployment directly with `X-API-Key` or bearer auth
5. Use `/healthz` for liveness and authenticated `/readyz` for dependency state

Do not place credentials in prompts, URLs, source files, exported reports, or
shell history. Use the environment or the user's approved secret store.
Never request or expose provider-owned server keys. Use `provider=auto` unless
the user needs a configured source for a specific evidence reason.

## Choose a capability

- Use ordinary search for a quick single-source lookup
- Use Evidence v1 for one to three queries that require provenance, bounded
  multi-source fusion, extraction, and structured partial-failure reporting
- Use extraction only for an already selected public URL
- Use an answer snapshot only when the user asks for a dated observation from a
  configured compatible model API
- Use `/openapi.json` only on an authenticated owned deployment when exact
  schemas are required; the public `/docs` page is intentionally introductory

## Preserve evidence semantics

For every researched claim:

1. Retain the source URL, observed time, query, and provider provenance
2. Distinguish direct page evidence from search snippets and model output
3. Report partial, degraded, timeout, auth, quota, and circuit-open states
4. Verify high-impact claims against primary sources when possible
5. Never describe a search result or answer snapshot as proof of visibility in
   ChatGPT, Perplexity, Gemini, Google AI Overview, or another consumer product

## Bound requests

Keep requests small by default: one focused query, up to eight results, no more
than two providers, and only the pages required for the conclusion. Expand only
when the first pass leaves a material evidence gap.

## Handle custom model APIs

Use request-scoped custom model credentials only when the user explicitly asks.
A bare compatible origin can be supplied without manually appending `/v1`.
Never persist or echo the request key or custom base URL. Preserve only the
model ID, answer, citations, timing, status, and stated limitations when the
calling workflow needs a dated snapshot.

## Report results

Lead with the answer, then list sources and limitations. If the gateway is
unavailable, explain the failed phase and whether it is retryable. Do not
silently switch to an unapproved public search service or invent missing
evidence.
