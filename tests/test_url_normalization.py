import asyncio

import pytest

from app.utils.errors import GatewayError
from app.utils.url_normalization import (
    content_hash,
    extract_canonical_url,
    normalize_answer_api_base_url,
    normalize_url,
    registrable_domain,
    source_id,
    validate_public_https_api_base_url,
    validate_public_http_url,
)


def test_normalize_url_removes_tracking_fragment_and_default_port():
    normalized = normalize_url(
        "HTTPS://Example.COM:443/path/?utm_source=test&b=2&fbclid=x&a=1#fragment"
    )

    assert normalized == "https://example.com/path?a=1&b=2"


def test_normalize_url_preserves_content_query_parameters():
    assert normalize_url("https://example.com/search?q=geo&page=2") == "https://example.com/search?page=2&q=geo"


def test_registrable_domain_uses_public_suffix_data():
    assert registrable_domain("https://blog.example.co.uk/post") == "example.co.uk"
    assert registrable_domain("subdomain.example.com") == "example.com"


def test_extract_canonical_url_resolves_relative_href():
    html = '<html><head><link rel="alternate canonical" href="../article/?utm_campaign=x"></head></html>'

    assert extract_canonical_url(html, "https://example.com/blog/page") == "https://example.com/article"


def test_content_and_source_hashes_are_deterministic():
    assert content_hash("one   two") == content_hash("one two")
    assert source_id("https://example.com") == source_id("https://example.com")


def test_private_url_is_rejected(monkeypatch):
    monkeypatch.setattr(
        "app.utils.url_normalization.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("127.0.0.1", 0))],
    )

    with pytest.raises(GatewayError, match="private"):
        validate_public_http_url("https://example.com")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://api.public-service.com", "https://api.public-service.com/v1"),
        ("https://api.public-service.com/", "https://api.public-service.com/v1"),
        ("https://api.public-service.com/v1", "https://api.public-service.com/v1"),
        ("https://api.public-service.com/api/v1", "https://api.public-service.com/api/v1"),
        (
            "https://api.public-service.com/v1/chat/completions/",
            "https://api.public-service.com/v1",
        ),
        (
            "https://api.public-service.com/chat/completions",
            "https://api.public-service.com/v1",
        ),
        ("https://api.public-service.com/v1/models", "https://api.public-service.com/v1"),
        ("https://api.public-service.com/api/v1/models", "https://api.public-service.com/api/v1"),
        ("https://api.public-service.com/openai", "https://api.public-service.com/openai"),
    ],
)
def test_answer_api_base_url_normalization(value, expected):
    assert normalize_answer_api_base_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "http://api.public-service.com/v1",
        "https://user:password@api.public-service.com/v1",
        "https://api.public-service.com:443/v1",
        "https://127.0.0.1/v1",
        "https://localhost/v1",
        "https://api.public-service.com/v1?token=secret",
        "https://api.public-service.com/v1#fragment",
        "https://api.public-service.com/\nv1",
    ],
)
def test_answer_api_base_url_rejects_unsafe_syntax(value):
    with pytest.raises(GatewayError) as exc_info:
        normalize_answer_api_base_url(value)

    assert exc_info.value.detail == {"code": "ANSWER_API_URL_INVALID", "retryable": False}
    assert value not in exc_info.value.message


def test_answer_api_base_url_requires_only_global_dns_answers(monkeypatch):
    monkeypatch.setattr(
        "app.utils.url_normalization.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("10.0.0.8", 443))],
    )

    with pytest.raises(GatewayError) as exc_info:
        asyncio.run(validate_public_https_api_base_url("https://api.public-service.com/v1"))

    assert exc_info.value.detail["code"] == "ANSWER_API_URL_INVALID"


def test_answer_api_base_url_accepts_global_dns_answers(monkeypatch):
    monkeypatch.setattr(
        "app.utils.url_normalization.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("8.8.8.8", 443))],
    )

    assert (
        asyncio.run(validate_public_https_api_base_url("https://api.public-service.com/v1/chat/completions"))
        == "https://api.public-service.com/v1"
    )
