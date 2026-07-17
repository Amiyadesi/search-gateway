from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import socket
import unicodedata
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import tldextract

from app.utils.errors import GatewayError


_TLD_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())
_TRACKING_EXACT = {
    "dclid",
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "msclkid",
    "ref_src",
    "s_cid",
    "vero_conv",
    "vero_id",
    "yclid",
}
_TRACKING_PREFIXES = ("utm_", "pk_", "ga_")
_RESERVED_API_HOSTS = {"localhost", "localhost.localdomain"}
_RESERVED_API_SUFFIXES = (
    ".example",
    ".home.arpa",
    ".internal",
    ".invalid",
    ".local",
    ".localhost",
    ".onion",
    ".test",
)


def normalize_url(value: str, base_url: str | None = None) -> str:
    candidate = urljoin(base_url, value) if base_url else value
    try:
        parsed = urlsplit(candidate.strip())
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    if parsed.username or parsed.password:
        return ""

    scheme = parsed.scheme.lower()
    try:
        hostname = parsed.hostname.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError:
        return ""

    try:
        port = parsed.port
    except ValueError:
        return ""
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{hostname}:{port}"
    else:
        netloc = hostname

    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    filtered_query = []
    for key, item_value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.casefold()
        if lowered in _TRACKING_EXACT or lowered.startswith(_TRACKING_PREFIXES):
            continue
        filtered_query.append((key, item_value))
    filtered_query.sort(key=lambda item: (item[0].casefold(), item[1]))
    return urlunsplit((scheme, netloc, path, urlencode(filtered_query, doseq=True), ""))


def registrable_domain(value: str) -> str:
    host = value
    if "://" in value:
        try:
            host = urlsplit(value).hostname or ""
        except ValueError:
            return ""
    host = host.lower().strip().strip(".")
    if not host:
        return ""
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    extracted = _TLD_EXTRACT(host)
    if extracted.domain and extracted.suffix:
        return f"{extracted.domain}.{extracted.suffix}"
    return extracted.domain or host


def domain_matches(value: str, expected: str) -> bool:
    actual = value.lower().strip(".")
    target = expected.lower().strip(".")
    return actual == target or actual.endswith(f".{target}")


def content_hash(content: str) -> str | None:
    normalized = " ".join(content.split())
    if not normalized:
        return None
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def source_id(canonical_url: str) -> str:
    digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
    return f"src_{digest[:24]}"


def validate_public_http_url(value: str, allow_private: bool = False) -> str:
    normalized = normalize_url(value)
    if not normalized:
        raise GatewayError("URL 只支持无凭据的 http/https 地址", status_code=422)
    if allow_private:
        return normalized

    hostname = urlsplit(normalized).hostname or ""
    if hostname in {"localhost", "localhost.localdomain"}:
        raise GatewayError("默认禁止 localhost/private URL", status_code=422)
    try:
        addresses = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise GatewayError("URL 域名无法解析", status_code=422) from exc
    for item in addresses:
        address = item[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise GatewayError("默认禁止 localhost/private URL", status_code=422)
    return normalized


def normalize_answer_api_base_url(value: str) -> str:
    """Normalize a client-selected OpenAI-compatible base URL without resolving it."""
    if not isinstance(value, str) or any(unicodedata.category(character).startswith("C") for character in value):
        raise _answer_api_url_error()
    try:
        parsed = urlsplit(value.strip())
    except (AttributeError, ValueError) as exc:
        raise _answer_api_url_error() from exc

    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        raise _answer_api_url_error()
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise _answer_api_url_error()
    try:
        if parsed.port is not None:
            raise _answer_api_url_error()
    except ValueError as exc:
        raise _answer_api_url_error() from exc

    try:
        hostname = parsed.hostname.encode("idna").decode("ascii").casefold().rstrip(".")
    except UnicodeError as exc:
        raise _answer_api_url_error() from exc
    if not hostname or "." not in hostname or "\\" in value:
        raise _answer_api_url_error()
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise _answer_api_url_error()
    if hostname in _RESERVED_API_HOSTS or hostname.endswith(_RESERVED_API_SUFFIXES):
        raise _answer_api_url_error()

    path = normalize_answer_api_path(parsed.path)
    return urlunsplit(("https", hostname, path, "", ""))


def normalize_answer_api_path(value: str | None) -> str:
    """Return an OpenAI-compatible API root path without an endpoint suffix."""
    path = (value or "").rstrip("/")
    lowered_path = path.casefold()
    for suffix in ("/chat/completions", "/models"):
        if lowered_path.endswith(suffix):
            path = path[: -len(suffix)].rstrip("/")
            break
    return path or "/v1"


async def validate_public_https_api_base_url(value: str) -> str:
    normalized = normalize_answer_api_base_url(value)
    hostname = urlsplit(normalized).hostname or ""
    try:
        await asyncio.to_thread(_validate_global_api_hostname, hostname)
    except GatewayError:
        raise
    except Exception as exc:
        raise _answer_api_url_error() from exc
    return normalized


def _validate_global_api_hostname(hostname: str) -> None:
    try:
        addresses = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except (OSError, socket.gaierror) as exc:
        raise _answer_api_url_error() from exc
    if not addresses:
        raise _answer_api_url_error()
    for item in addresses:
        try:
            address = ipaddress.ip_address(item[4][0])
        except (IndexError, TypeError, ValueError) as exc:
            raise _answer_api_url_error() from exc
        if not address.is_global:
            raise _answer_api_url_error()


def _answer_api_url_error() -> GatewayError:
    return GatewayError(
        "Answer API base URL is invalid",
        status_code=422,
        detail={"code": "ANSWER_API_URL_INVALID", "retryable": False},
    )


class _CanonicalParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.href or tag.casefold() != "link":
            return
        values = {key.casefold(): (value or "") for key, value in attrs}
        rel = {part.casefold() for part in values.get("rel", "").split()}
        if "canonical" in rel:
            self.href = values.get("href", "")


def extract_canonical_url(raw_html: str, base_url: str) -> str:
    if not raw_html:
        return ""
    parser = _CanonicalParser()
    try:
        parser.feed(raw_html[:2_000_000])
    except Exception:
        return ""
    return normalize_url(parser.href, base_url=base_url) if parser.href else ""
