"""Tests for the environment-gated provider factory (Constraint #4)."""

from __future__ import annotations

import pytest

from rag_core.config import (
    ConfigurationError,
    Environment,
    LLMProvider,
    LLMProviderFactory,
    Settings,
)


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "environment": Environment.DEV,
        "llm_provider": LLMProvider.GROQ_FREE,
        "groq_api_key": "test-key",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_production_rejects_free_tier() -> None:
    """Production + a free-tier provider must refuse to boot."""
    settings = _settings(
        environment=Environment.PRODUCTION, llm_provider=LLMProvider.GROQ_FREE
    )
    with pytest.raises(ConfigurationError, match="free-tier"):
        LLMProviderFactory.validate_environment(settings)


def test_production_allows_paid_tier() -> None:
    """Production + a paid provider passes the environment gate."""
    settings = _settings(
        environment=Environment.PRODUCTION,
        llm_provider=LLMProvider.OPENAI,
        openai_api_key="sk-test",
    )
    # Should not raise.
    LLMProviderFactory.validate_environment(settings)


def test_dev_allows_free_tier() -> None:
    """Dev + a free-tier provider is allowed."""
    LLMProviderFactory.validate_environment(_settings())


def test_missing_credentials_rejected() -> None:
    """Selecting a provider without its credential fails fast."""
    settings = _settings(
        environment=Environment.DEV,
        llm_provider=LLMProvider.OPENAI,
        groq_api_key=None,
    )
    with pytest.raises(ConfigurationError, match="openai_api_key"):
        LLMProviderFactory(settings)
