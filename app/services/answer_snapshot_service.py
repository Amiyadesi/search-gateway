from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.config import Settings
from app.schemas.evidence import (
    AnswerApiError,
    AnswerCitation,
    AnswerModelsRequest,
    AnswerModelsResponse,
    AnswerObservation,
    AnswerPhaseTiming,
    AnswerSnapshotRequest,
    AnswerSnapshotResponse,
    AnswerSnapshotUsage,
    AnswerTimeoutPhase,
    sanitize_model_id,
    sanitize_usage,
)
from app.utils.errors import GatewayError
from app.utils.http import build_client, retry_after_seconds
from app.utils.url_normalization import (
    normalize_answer_api_path,
    normalize_url,
    validate_public_https_api_base_url,
)


class _AnswerApiRedirectError(Exception):
    pass


@dataclass(frozen=True)
class _AnswerConfig:
    base_url: str
    model: str
    api_key: str
    api_id: str


class _AnswerTimingTrace:
    _EVENT_FIELDS = (
        (".connect_tcp", "connect_ms"),
        (".connect_unix_socket", "connect_ms"),
        (".start_tls", "connect_ms"),
        (".send_request_headers", "request_write_ms"),
        (".send_request_body", "request_write_ms"),
        (".receive_response_headers", "upstream_wait_ms"),
        (".receive_response_body", "response_read_ms"),
    )
    _TIMEOUT_PHASES: dict[str, AnswerTimeoutPhase] = {
        "connect_ms": "connect",
        "request_write_ms": "write",
        "upstream_wait_ms": "upstream",
        "response_read_ms": "read",
    }

    def __init__(self, clock: Callable[[], float] = time.perf_counter) -> None:
        self._clock = clock
        self._started: dict[str, tuple[float, str]] = {}
        self._durations_ms: dict[str, float] = {}
        self._last_failed_phase: AnswerTimeoutPhase | None = None

    async def __call__(self, event: str, _info: dict[str, Any]) -> None:
        self.record(event)

    def record(self, event: str, at: float | None = None) -> None:
        operation, separator, lifecycle = event.rpartition(".")
        if not separator or lifecycle not in {"started", "complete", "failed"}:
            return
        field = self._field_for_operation(operation)
        if field is None:
            return
        observed_at = self._clock() if at is None else at
        if lifecycle == "started":
            self._started[operation] = (observed_at, field)
            return
        started = self._started.pop(operation, None)
        if started is not None:
            started_at, started_field = started
            elapsed_ms = max(0.0, (observed_at - started_at) * 1000)
            self._durations_ms[started_field] = self._durations_ms.get(started_field, 0.0) + elapsed_ms
        if lifecycle == "failed":
            self._last_failed_phase = self._TIMEOUT_PHASES[field]
        elif lifecycle == "complete":
            self._last_failed_phase = None

    def snapshot(self, total_ms: int) -> AnswerPhaseTiming:
        return AnswerPhaseTiming(
            connect_ms=self._rounded("connect_ms"),
            request_write_ms=self._rounded("request_write_ms"),
            upstream_wait_ms=self._rounded("upstream_wait_ms"),
            response_read_ms=self._rounded("response_read_ms"),
            total_ms=total_ms,
        )

    def timeout_phase(self) -> AnswerTimeoutPhase | None:
        if self._last_failed_phase is not None:
            return self._last_failed_phase
        active_fields = {field for _, field in self._started.values()}
        for field in ("upstream_wait_ms", "response_read_ms", "request_write_ms", "connect_ms"):
            if field in active_fields:
                return self._TIMEOUT_PHASES[field]
        return None

    @classmethod
    def _field_for_operation(cls, operation: str) -> str | None:
        for suffix, field in cls._EVENT_FIELDS:
            if operation.endswith(suffix):
                return field
        return None

    def _rounded(self, field: str) -> int | None:
        value = self._durations_ms.get(field)
        return None if value is None else max(0, round(value))


class AnswerSnapshotService:
    """Zero-persistence observer for fixed or request-scoped OpenAI-compatible APIs."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def observe(
        self,
        request: AnswerSnapshotRequest,
        *,
        request_api_key: str | None = None,
    ) -> AnswerSnapshotResponse:
        config = await self._resolve_config(request, request_api_key=request_api_key)
        started = time.perf_counter()
        observed_at = datetime.now(UTC)
        observations = await asyncio.gather(
            *(
                self._observe_one(
                    query,
                    request.locale,
                    config,
                )
                for query in request.queries
            )
        )
        errors = [item.error for item in observations if item.error is not None]
        successful = sum(item.status == "complete" for item in observations)
        provider_usage: dict[str, int | float] = {}
        for observation in observations:
            for key, value in observation.usage.items():
                provider_usage[key] = provider_usage.get(key, 0) + value
        return AnswerSnapshotResponse(
            success=successful > 0,
            request_id=f"ans_{secrets.token_hex(8)}",
            observed_at=observed_at,
            api_id=config.api_id,
            model=config.model,
            observations=observations,
            usage=AnswerSnapshotUsage(
                api_calls=len(observations),
                successful_calls=successful,
                elapsed_ms=max(0, round((time.perf_counter() - started) * 1000)),
                provider_usage=provider_usage,
            ),
            partial=0 < successful < len(observations),
            degraded=bool(errors),
            errors=errors,
            limitations=[
                "This is a dated API answer observation, not a result from any provider's consumer interface.",
                "Answers and citations may vary by model version, locale, region, account, and observation time.",
                "The submitted API key and custom base URL are used only for this request and are not persisted or returned.",
            ],
        )

    async def list_models(
        self,
        request: AnswerModelsRequest,
        *,
        request_api_key: str | None = None,
    ) -> AnswerModelsResponse:
        api_key = self._require_request_key(request_api_key)
        base_url = await validate_public_https_api_base_url(request.api_base_url)
        try:
            async with build_client(
                self.settings,
                timeout=self.settings.answer_api_timeout_seconds,
                follow_redirects=False,
            ) as client:
                response = await client.get(
                    self._api_url(base_url, "models"),
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Accept": "application/json",
                    },
                )
                self._raise_for_status(response)
                data = response.json()
            return AnswerModelsResponse(models=self._model_ids(data))
        except Exception as exc:
            raise self._gateway_error(exc) from None

    async def _resolve_config(
        self,
        request: AnswerSnapshotRequest,
        *,
        request_api_key: str | None,
    ) -> _AnswerConfig:
        if request.api_base_url is not None and request.api_model is not None:
            api_key = self._require_request_key(request_api_key)
            return _AnswerConfig(
                base_url=await validate_public_https_api_base_url(request.api_base_url),
                model=request.api_model,
                api_key=api_key,
                api_id="request_api",
            )

        base_url = self._configured_base_url(self.settings.answer_api_base_url)
        model = sanitize_model_id(self.settings.answer_api_model)
        if not base_url or not model:
            raise GatewayError(
                "Answer API endpoint is not configured",
                status_code=503,
                detail={"code": "ANSWER_API_UNAVAILABLE", "retryable": False},
            )
        api_key = self.settings.answer_api_key.strip()
        if not api_key:
            raise GatewayError(
                "Answer API key is not configured",
                status_code=503,
                detail={"code": "ANSWER_API_KEY_REQUIRED", "retryable": False},
            )
        return _AnswerConfig(
            base_url=base_url,
            model=model,
            api_key=api_key,
            api_id=self.settings.answer_api_id,
        )

    async def _observe_one(
        self,
        query: str,
        locale: str,
        config: _AnswerConfig,
    ) -> AnswerObservation:
        started = time.perf_counter()
        observed_at = datetime.now(UTC)
        timing_trace = _AnswerTimingTrace()
        try:
            async with build_client(
                self.settings,
                timeout=self.settings.answer_api_timeout_seconds,
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    self._api_url(config.base_url, "chat/completions"),
                    headers={
                        "Authorization": f"Bearer {config.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": config.model,
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "Answer accurately in the requested locale. Preserve source URLs returned by the API "
                                    "and do not invent citations."
                                ),
                            },
                            {"role": "user", "content": f"Locale: {locale}\n\n{query}"},
                        ],
                        "temperature": 0.2,
                        "max_tokens": self.settings.answer_api_max_tokens,
                    },
                    extensions={"trace": timing_trace},
                )
                self._raise_for_status(response)
                data = response.json()
            answer = self._answer_text(data)
            if not answer:
                raise ValueError("missing answer text")
            observed_model = sanitize_model_id(data.get("model")) if isinstance(data, dict) else ""
            elapsed_ms = max(0, round((time.perf_counter() - started) * 1000))
            return AnswerObservation(
                query=query,
                status="complete",
                api_id=config.api_id,
                model=observed_model or config.model,
                observed_at=observed_at,
                latency_ms=elapsed_ms,
                answer=answer,
                citations=self._citations(data),
                usage=sanitize_usage(data.get("usage") if isinstance(data, dict) else None),
                timing=timing_trace.snapshot(elapsed_ms),
            )
        except Exception as exc:
            elapsed_ms = max(0, round((time.perf_counter() - started) * 1000))
            error = self._classify_error(
                exc,
                query,
                observed_timeout_phase=timing_trace.timeout_phase(),
            )
            return AnswerObservation(
                query=query,
                status="error",
                api_id=config.api_id,
                model=config.model,
                observed_at=observed_at,
                latency_ms=elapsed_ms,
                timing=timing_trace.snapshot(elapsed_ms),
                error=error,
            )

    @staticmethod
    def _configured_base_url(value: str) -> str:
        candidate = value.strip().rstrip("/")
        if not candidate:
            return ""
        try:
            parsed = urlsplit(candidate)
            port = parsed.port
        except ValueError:
            return ""
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
            return ""
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            return ""
        path = normalize_answer_api_path(parsed.path)
        hostname = parsed.hostname.casefold().rstrip(".")
        netloc = f"{hostname}:{port}" if port is not None else hostname
        return urlunsplit((parsed.scheme.casefold(), netloc, path, "", ""))

    @staticmethod
    def _api_url(base_url: str, path: str) -> str:
        return f"{base_url.rstrip('/')}/{path.lstrip('/')}"

    @staticmethod
    def _require_request_key(value: str | None) -> str:
        key = (value or "").strip()
        if not key:
            raise GatewayError(
                "X-Answer-API-Key is required for a custom Answer API",
                status_code=400,
                detail={"code": "ANSWER_API_KEY_REQUIRED", "retryable": False},
            )
        return key

    @staticmethod
    def _raise_for_status(response: Any) -> None:
        if 300 <= int(response.status_code) < 400:
            raise _AnswerApiRedirectError()
        response.raise_for_status()

    @staticmethod
    def _answer_text(data: Any) -> str:
        if not isinstance(data, dict):
            return ""
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            return ""
        message = choices[0].get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content.strip()
        text = choices[0].get("text")
        return text.strip() if isinstance(text, str) else ""

    @classmethod
    def _citations(cls, data: Any) -> list[AnswerCitation]:
        if not isinstance(data, dict):
            return []
        raw_items: list[Any] = []
        for key in ("citations", "sources", "search_results"):
            value = data.get(key)
            if isinstance(value, list):
                raw_items.extend(value)
        choices = data.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            if isinstance(message, dict):
                for key in ("citations", "annotations"):
                    value = message.get(key)
                    if isinstance(value, list):
                        raw_items.extend(value)

        citations: list[AnswerCitation] = []
        seen: set[str] = set()
        for item in raw_items:
            url, title, snippet = cls._citation_fields(item)
            normalized = normalize_url(url)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            citations.append(AnswerCitation(url=normalized, title=title, snippet=snippet))
        return citations[:20]

    @staticmethod
    def _citation_fields(item: Any) -> tuple[str, str, str]:
        if isinstance(item, str):
            return item, "", ""
        if not isinstance(item, dict):
            return "", "", ""
        nested = item.get("url_citation") if isinstance(item.get("url_citation"), dict) else {}
        url = item.get("url") or item.get("href") or nested.get("url") or ""
        title = item.get("title") or nested.get("title") or ""
        snippet = item.get("snippet") or item.get("text") or nested.get("snippet") or ""
        return str(url), str(title), str(snippet)

    @staticmethod
    def _model_ids(data: Any) -> list[str]:
        if not isinstance(data, dict):
            raise ValueError("invalid model list")
        raw_items = data.get("data")
        if not isinstance(raw_items, list):
            raw_items = data.get("models")
        if not isinstance(raw_items, list):
            raise ValueError("invalid model list")

        models: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            raw_id = item.get("id") if isinstance(item, dict) else item
            model_id = sanitize_model_id(raw_id)
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            models.append(model_id)
            if len(models) >= 100:
                break
        return models

    @classmethod
    def _gateway_error(cls, exc: Exception) -> GatewayError:
        code, retryable, message, retry_after, timeout_phase = cls._error_details(exc)
        detail: dict[str, Any] = {"code": code, "retryable": retryable}
        if retry_after is not None:
            detail["retry_after_seconds"] = retry_after
        if timeout_phase is not None:
            detail["timeout_phase"] = timeout_phase
        return GatewayError(
            message,
            status_code=503 if retryable else 502,
            detail=detail,
        )

    @classmethod
    def _classify_error(
        cls,
        exc: Exception,
        query: str,
        *,
        observed_timeout_phase: AnswerTimeoutPhase | None = None,
    ) -> AnswerApiError:
        code, retryable, message, retry_after, timeout_phase = cls._error_details(
            exc,
            observed_timeout_phase=observed_timeout_phase,
        )
        return AnswerApiError(
            code=code,
            scope="answer_api",
            stage="attribution",
            retryable=retryable,
            message=message,
            query=query,
            retry_after_seconds=retry_after,
            timeout_phase=timeout_phase,
        )

    @staticmethod
    def _error_details(
        exc: Exception,
        *,
        observed_timeout_phase: AnswerTimeoutPhase | None = None,
    ) -> tuple[str, bool, str, int | None, AnswerTimeoutPhase | None]:
        retry_after: int | None = None
        timeout_phase: AnswerTimeoutPhase | None = None
        if isinstance(exc, _AnswerApiRedirectError):
            code, retryable, message = (
                "ANSWER_API_REDIRECT_BLOCKED",
                False,
                "The configured API attempted a redirect, which is not allowed.",
            )
        elif isinstance(exc, httpx.ConnectTimeout):
            code, retryable, message = "ANSWER_API_TIMEOUT", True, "The configured API connection timed out."
            timeout_phase = observed_timeout_phase or "connect"
        elif isinstance(exc, httpx.WriteTimeout):
            code, retryable, message = (
                "ANSWER_API_TIMEOUT",
                True,
                "The configured API request timed out while being sent.",
            )
            timeout_phase = observed_timeout_phase or "write"
        elif isinstance(exc, httpx.ReadTimeout):
            code, retryable, message = (
                "ANSWER_API_TIMEOUT",
                True,
                "The configured API response timed out while being read.",
            )
            timeout_phase = observed_timeout_phase or "read"
        elif isinstance(exc, httpx.PoolTimeout):
            code, retryable, message = "ANSWER_API_TIMEOUT", True, "The configured API connection pool timed out."
            timeout_phase = observed_timeout_phase or "pool"
        elif isinstance(exc, httpx.TimeoutException):
            code, retryable, message = "ANSWER_API_TIMEOUT", True, "The configured API timed out."
            timeout_phase = observed_timeout_phase or "unknown"
        elif isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            if status in {401, 403}:
                code, retryable, message = "ANSWER_API_AUTH_ERROR", False, "The configured API rejected the key."
            elif status == 429:
                code, retryable, message = "ANSWER_API_RATE_LIMITED", True, "The configured API is rate limited."
                retry_after = retry_after_seconds(exc.response)
            elif status == 408:
                code, retryable, message = (
                    "ANSWER_API_TIMEOUT",
                    True,
                    "The configured API reported an upstream timeout.",
                )
                timeout_phase = "upstream"
            elif status == 504:
                code, retryable, message = (
                    "ANSWER_API_TIMEOUT",
                    True,
                    "The configured API gateway reported a timeout.",
                )
                timeout_phase = "gateway"
            elif status >= 500:
                code, retryable, message = "ANSWER_API_UPSTREAM_ERROR", True, "The configured API is unavailable."
            else:
                code, retryable, message = (
                    "ANSWER_API_INVALID_REQUEST",
                    False,
                    "The configured API rejected the request.",
                )
        elif isinstance(exc, httpx.HTTPError):
            code, retryable, message = (
                "ANSWER_API_NETWORK_ERROR",
                True,
                "The configured API could not be reached.",
            )
        else:
            code, retryable, message = (
                "ANSWER_API_MALFORMED_RESPONSE",
                True,
                "The configured API returned an invalid response.",
            )
        return code, retryable, message, retry_after, timeout_phase
