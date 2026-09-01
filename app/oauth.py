"""Small OAuth 2.1 primitives for the first-party MCP authorization server."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


class OAuthError(ValueError):
    """An OAuth protocol error safe to return to a client."""

    def __init__(self, code: str, description: str, *, status_code: int = 400) -> None:
        self.code = code
        self.description = description
        self.status_code = status_code
        super().__init__(description)


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    if not value or len(value) > 16384:
        raise ValueError("invalid encoded value")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _signed(prefix: str, payload: dict[str, Any], secret: str) -> str:
    raw = _b64encode(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(secret.encode("utf-8"), f"{prefix}.{raw}".encode("ascii"), hashlib.sha256).digest()
    return f"{prefix}.{raw}.{_b64encode(signature)}"


def _verify_signed(value: str, prefix: str, secret: str) -> dict[str, Any]:
    try:
        actual_prefix, encoded, signature = value.split(".", 2)
        if actual_prefix != prefix:
            raise ValueError
        expected = hmac.new(
            secret.encode("utf-8"),
            f"{prefix}.{encoded}".encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(_b64decode(signature), expected):
            raise ValueError
        payload = json.loads(_b64decode(encoded).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError
        return payload
    except (ValueError, TypeError, binascii.Error, json.JSONDecodeError, UnicodeError) as exc:
        raise OAuthError("invalid_token", "无效 OAuth 凭据", status_code=401) from exc


def _now() -> int:
    return int(time.time())


def canonical_issuer(value: str) -> str:
    issuer = value.strip().rstrip("/")
    parsed = urlparse(issuer)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("MCP_OAUTH_ISSUER 必须是无 query/fragment 的 HTTPS URL")
    return issuer


def allowed_resources(issuer: str) -> set[str]:
    base = canonical_issuer(issuer)
    return {base, f"{base}/mcp/search", f"{base}/mcp/fns"}


def validate_resource(resource: str | None, issuer: str) -> str:
    if not isinstance(resource, str) or not resource.strip():
        raise OAuthError("invalid_request", "缺少 resource 参数")
    value = resource.strip().rstrip("/")
    if value not in allowed_resources(issuer):
        raise OAuthError("invalid_target", "resource 不是此 MCP 网关")
    return value


def validate_redirect_uri(uri: Any) -> str:
    if not isinstance(uri, str) or not uri or len(uri) > 2048:
        raise OAuthError("invalid_request", "redirect_uri 无效")
    parsed = urlparse(uri)
    if parsed.fragment or parsed.query or parsed.username is not None or parsed.password is not None:
        raise OAuthError("invalid_request", "redirect_uri 不得包含 query 或 fragment")
    host = (parsed.hostname or "").lower()
    is_loopback = host in {"localhost", "127.0.0.1", "::1"}
    is_openai = host == "chatgpt.com" or host.endswith(".chatgpt.com") or host == "chat.openai.com"
    if parsed.scheme != "https" and not (is_loopback and parsed.scheme == "http"):
        raise OAuthError("invalid_request", "redirect_uri 必须使用 HTTPS")
    if not (is_loopback or is_openai):
        raise OAuthError("invalid_request", "redirect_uri 主机不在允许列表")
    return uri


def validate_pkce_challenge(challenge: Any, method: Any) -> str:
    if not isinstance(challenge, str) or not 43 <= len(challenge) <= 128:
        raise OAuthError("invalid_request", "必须提供 PKCE code_challenge")
    if method != "S256":
        raise OAuthError("invalid_request", "仅支持 PKCE S256")
    return challenge


def verify_pkce(verifier: Any, challenge: str) -> bool:
    if not isinstance(verifier, str) or not 43 <= len(verifier) <= 128:
        return False
    try:
        digest = _b64encode(hashlib.sha256(verifier.encode("ascii", "strict")).digest())
    except UnicodeError:
        return False
    return hmac.compare_digest(digest, challenge)


def issue_client_id(
    redirect_uris: list[str],
    secret: str,
    *,
    client_name: str = "MCP client",
    ttl_seconds: int = 31_536_000,
) -> str:
    now = _now()
    payload = {
        "redirect_uris": redirect_uris,
        "client_name": client_name[:128],
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return _signed("cid", payload, secret)


def read_client_id(client_id: Any, secret: str) -> dict[str, Any]:
    if not isinstance(client_id, str):
        raise OAuthError("invalid_client", "client_id 无效", status_code=401)
    try:
        payload = _verify_signed(client_id, "cid", secret)
    except OAuthError as exc:
        raise OAuthError("invalid_client", "client_id 无效", status_code=401) from exc
    redirect_uris = payload.get("redirect_uris")
    if (
        not isinstance(redirect_uris, list)
        or not redirect_uris
        or any(not isinstance(item, str) for item in redirect_uris)
        or not isinstance(payload.get("exp"), int)
        or payload["exp"] < _now()
    ):
        raise OAuthError("invalid_client", "client_id 已失效", status_code=401)
    return payload


def issue_authorization_code(
    store: "AuthorizationCodeStore",
    *,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    resource: str,
    scope: str,
    subject: str,
    ttl_seconds: int = 300,
) -> str:
    code = secrets.token_urlsafe(32)
    store.put(
        code,
        AuthorizationCode(
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            resource=resource,
            scope=scope,
            subject=subject,
            expires_at=time.time() + ttl_seconds,
        ),
    )
    return code


def issue_access_token(
    *,
    secret: str,
    issuer: str,
    resource: str,
    scope: str,
    subject: str,
    ttl_seconds: int,
) -> str:
    now = _now()
    return _signed(
        "at",
        {
            "iss": canonical_issuer(issuer),
            "aud": resource,
            "scope": scope,
            "sub": subject,
            "iat": now,
            "exp": now + ttl_seconds,
            "jti": secrets.token_urlsafe(12),
        },
        secret,
    )


def verify_access_token(
    token: str,
    *,
    secret: str,
    issuer: str,
    resource: str,
    required_scope: str = "mcp",
) -> dict[str, Any]:
    payload = _verify_signed(token, "at", secret)
    now = _now()
    if (
        payload.get("iss") != canonical_issuer(issuer)
        or not isinstance(payload.get("exp"), int)
        or payload["exp"] < now
    ):
        raise OAuthError("invalid_token", "OAuth access token 已失效", status_code=401)
    audience = payload.get("aud")
    if audience not in allowed_resources(issuer) or (audience != canonical_issuer(issuer) and audience != resource):
        raise OAuthError("invalid_token", "OAuth access token audience 不匹配", status_code=401)
    scopes = set(str(payload.get("scope", "")).split())
    if required_scope and required_scope not in scopes:
        raise OAuthError("insufficient_scope", "OAuth access token 缺少所需 scope", status_code=403)
    return payload


@dataclass(frozen=True)
class AuthorizationCode:
    client_id: str
    redirect_uri: str
    code_challenge: str
    resource: str
    scope: str
    subject: str
    expires_at: float


class AuthorizationCodeStore:
    """In-memory one-shot codes; codes are intentionally short-lived and non-persistent."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._codes: dict[str, AuthorizationCode] = {}

    def put(self, code: str, value: AuthorizationCode) -> None:
        with self._lock:
            self._purge_locked()
            self._codes[code] = value

    def pop(self, code: str) -> AuthorizationCode | None:
        with self._lock:
            self._purge_locked()
            return self._codes.pop(code, None)

    def _purge_locked(self) -> None:
        now = time.time()
        for key, value in list(self._codes.items()):
            if value.expires_at <= now:
                self._codes.pop(key, None)


AUTHORIZATION_CODES = AuthorizationCodeStore()


__all__ = [
    "AUTHORIZATION_CODES",
    "AuthorizationCode",
    "AuthorizationCodeStore",
    "OAuthError",
    "allowed_resources",
    "canonical_issuer",
    "issue_access_token",
    "issue_authorization_code",
    "issue_client_id",
    "read_client_id",
    "validate_pkce_challenge",
    "validate_redirect_uri",
    "validate_resource",
    "verify_access_token",
    "verify_pkce",
]
