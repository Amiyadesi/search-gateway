"""Small in-process security primitives for the public MCP endpoints."""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any


class McpAuthError(ValueError):
    """Raised when the public MCP bearer token is missing or invalid."""


class McpRateLimitError(ValueError):
    """Raised when one token/client exceeds the MCP request budget."""


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def extract_bearer(authorization: str | None) -> str:
    if not authorization:
        return ""
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return value.strip()


def validate_access_token(authorization: str | None, expected: str) -> str:
    token = extract_bearer(authorization)
    if not expected or not token or not secrets.compare_digest(token, expected):
        raise McpAuthError("MCP 未授权")
    return token


class McpRateLimiter:
    """Fixed one-minute window; the API runs as one public container."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[str, deque[float]] = defaultdict(deque)

    def check(self, identity: str, limit: int) -> None:
        now = time.monotonic()
        cutoff = now - 60.0
        with self._lock:
            queue = self._requests[identity]
            while queue and queue[0] <= cutoff:
                queue.popleft()
            if len(queue) >= limit:
                raise McpRateLimitError("MCP 请求过于频繁")
            queue.append(now)


@dataclass(frozen=True)
class Confirmation:
    token_fingerprint: str
    request_digest: str
    expires_at: float


class ConfirmationStore:
    """One-shot confirmation IDs; raw arguments are never retained."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, Confirmation] = {}

    @staticmethod
    def digest(arguments: dict[str, Any]) -> str:
        import json

        raw = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def issue(self, token: str, arguments: dict[str, Any], ttl_seconds: int) -> tuple[str, int]:
        confirmation_id = secrets.token_urlsafe(24)
        item = Confirmation(
            token_fingerprint(token),
            self.digest(arguments),
            time.time() + ttl_seconds,
        )
        with self._lock:
            self._purge_locked()
            self._items[confirmation_id] = item
        return confirmation_id, ttl_seconds

    def consume(self, confirmation_id: str, token: str, arguments: dict[str, Any]) -> bool:
        with self._lock:
            self._purge_locked()
            item = self._items.get(confirmation_id)
            if not item:
                return False
            if item.token_fingerprint != token_fingerprint(token):
                return False
            if item.request_digest != self.digest(arguments):
                return False
            self._items.pop(confirmation_id, None)
            return True

    def _purge_locked(self) -> None:
        now = time.time()
        for key, item in list(self._items.items()):
            if item.expires_at <= now:
                self._items.pop(key, None)


MCP_RATE_LIMITER = McpRateLimiter()
MCP_CONFIRMATIONS = ConfirmationStore()


__all__ = [
    "MCP_CONFIRMATIONS",
    "MCP_RATE_LIMITER",
    "ConfirmationStore",
    "McpAuthError",
    "McpRateLimitError",
    "extract_bearer",
    "token_fingerprint",
    "validate_access_token",
]
