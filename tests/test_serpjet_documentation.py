from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_serpjet_and_supported_service_docs_cover_setup_privacy_and_ai_skill():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    api = (ROOT / "docs" / "API.md").read_text(encoding="utf-8")
    providers = (ROOT / "docs" / "PROVIDERS.md").read_text(encoding="utf-8")
    privacy = (ROOT / "docs" / "PRIVACY.md").read_text(encoding="utf-8")
    skill = (ROOT / "skills" / "use-search-gateway" / "SKILL.md").read_text(encoding="utf-8")

    for text in (readme, api, providers, privacy):
        assert "SERPJET_API_KEYS" in text
        assert "https://serpjet.io/docs.html" in text
        assert "X-API-KEY" in text

    assert "skills/use-search-gateway/SKILL.md" in readme
    assert "Brave Search API" in providers
    assert "SearXNG" in providers
    assert "GPT4Free" in providers
    assert "GPL-3.0" in providers
    assert "1,000" in providers
    assert "12 months" in privacy
    assert "provider=auto" in skill
    assert "Never request or expose provider-owned server keys" in skill


def test_example_config_keeps_serpjet_credentials_empty_and_last_in_evidence_order():
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "SERPJET_API_KEYS=\n" in env_example
    assert "SERPJET_TIMEOUT_SECONDS=12" in env_example
    assert "EVIDENCE_PROVIDER_ORDER=brave,tavily,zhihu,exa,searxng,tavily_hikari,grok,serpjet" in env_example
