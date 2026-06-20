"""Application settings, logging, and the environment-gated LLM provider factory.

This module is the single source of truth for runtime configuration. The most
important behaviour here is the *fail-fast* gate in :class:`LLMProviderFactory`:
a production deployment is refused at startup if it is wired to a free-tier LLM
provider. See Architectural Constraint #4 ("LLM provider selection is
environment-gated, not just configurable").
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

logger = logging.getLogger("rag_core.config")


class ConfigurationError(RuntimeError):
    """Raised when the runtime configuration is invalid or unsafe to boot.

    This is intentionally a hard failure: the service must refuse to start
    rather than silently degrade (e.g. running a legal-audit workload on a
    rate-limited free tier in production).
    """


class Environment(StrEnum):
    """Deployment environment, controlling the provider safety gate."""

    DEV = "dev"
    STAGING = "staging"
    PRODUCTION = "production"


class LLMProvider(StrEnum):
    """Supported LLM providers.

    Free-tier providers are acceptable in ``dev``/``staging`` but are rejected
    in ``production`` by :meth:`LLMProviderFactory.validate_environment`.
    """

    GROQ_FREE = "groq_free"
    GEMINI_FREE = "gemini_free"
    GROQ_PAID = "groq_paid"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    AZURE_OPENAI = "azure_openai"


#: Providers that must never back a production deployment.
FREE_TIER_PROVIDERS: frozenset[LLMProvider] = frozenset(
    {LLMProvider.GROQ_FREE, LLMProvider.GEMINI_FREE}
)


class Settings(BaseSettings):
    """Strongly-typed runtime settings, loaded from environment / ``.env``.

    Attributes:
        environment: Active deployment environment.
        llm_provider: Selected LLM provider; gated against ``environment``.
        log_dir: Directory for rotating log files.
        log_level: Root log level for ``rag_core`` loggers.
        max_upload_bytes: Hard cap on accepted upload size.
        max_pages: Hard cap on accepted PDF page count.
        ocr_char_threshold: Mean chars/page below which native text is deemed
            insufficient and OCR failover is triggered.
        chroma_persist_dir: On-disk persistence path for ChromaDB.
        embedding_model: Local sentence-transformers model id (no external call).
        sqlite_db_path: On-disk path for the audit-results SQLite database.
        access_code: Shared application access code (used by the web gate).
        openai_api_key / anthropic_api_key / groq_api_key / google_api_key:
            Provider credentials, only the active one is required.
        azure_openai_endpoint / azure_openai_deployment: Azure routing config.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Core ----------------------------------------------------------------
    environment: Environment = Environment.DEV
    llm_provider: LLMProvider = LLMProvider.GROQ_FREE
    log_dir: Path = Path("logs")
    log_level: str = "INFO"

    # --- Upload security -----------------------------------------------------
    max_upload_bytes: int = Field(default=25 * 1024 * 1024, ge=1)  # 25 MiB
    max_pages: int = Field(default=300, ge=1)

    # --- Processing ----------------------------------------------------------
    ocr_char_threshold: int = Field(default=100, ge=0)

    # --- Storage -------------------------------------------------------------
    chroma_persist_dir: Path = Path("data/chroma")
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    # Relative by default so local dev works on any OS; Docker overrides this to
    # an absolute path under a mounted volume (see docker-compose.yml).
    sqlite_db_path: Path = Path("data/audits/audit_store.db")

    # --- Generation tuning ---------------------------------------------------
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    llm_max_retries: int = Field(default=5, ge=0)

    # --- Access gate ---------------------------------------------------------
    access_code: SecretStr = SecretStr("change-me-locally")

    # --- Provider credentials ------------------------------------------------
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    google_api_key: SecretStr | None = None
    azure_openai_api_key: SecretStr | None = None
    azure_openai_endpoint: str | None = None
    azure_openai_deployment: str | None = None
    azure_openai_api_version: str = "2024-06-01"

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        """Ensure ``log_level`` names a real ``logging`` level."""
        upper = value.upper()
        if upper not in logging.getLevelNamesMapping():
            raise ValueError(f"Unknown log level: {value!r}")
        return upper


class LLMProviderFactory:
    """Resolves the active chat model from settings, with a production gate.

    The factory enforces the environment gate at construction time so an
    unsafe deployment fails before serving any request.
    """

    def __init__(self, settings: Settings) -> None:
        """Validate the environment/provider pairing and store settings.

        Args:
            settings: The loaded application settings.

        Raises:
            ConfigurationError: If ``production`` is paired with a free-tier
                provider, or the active provider's credentials are missing.
        """
        self._settings = settings
        self.validate_environment(settings)
        self._assert_credentials_present(settings)

    @staticmethod
    def validate_environment(settings: Settings) -> None:
        """Reject free-tier providers in production.

        Args:
            settings: The loaded application settings.

        Raises:
            ConfigurationError: If running in production with a free-tier
                provider.
        """
        if (
            settings.environment is Environment.PRODUCTION
            and settings.llm_provider in FREE_TIER_PROVIDERS
        ):
            raise ConfigurationError(
                "Refusing to boot: ENVIRONMENT=production cannot use free-tier "
                f"LLM_PROVIDER={settings.llm_provider.value!r}. Configure a paid "
                "provider (openai, anthropic, azure_openai, or groq_paid)."
            )

    @staticmethod
    def _assert_credentials_present(settings: Settings) -> None:
        """Verify the active provider has the credentials it needs.

        Raises:
            ConfigurationError: If a required credential is absent.
        """
        provider = settings.llm_provider
        required: dict[LLMProvider, tuple[str, Any]] = {
            LLMProvider.OPENAI: ("openai_api_key", settings.openai_api_key),
            LLMProvider.ANTHROPIC: ("anthropic_api_key", settings.anthropic_api_key),
            LLMProvider.GROQ_FREE: ("groq_api_key", settings.groq_api_key),
            LLMProvider.GROQ_PAID: ("groq_api_key", settings.groq_api_key),
            LLMProvider.GEMINI_FREE: ("google_api_key", settings.google_api_key),
        }
        if provider is LLMProvider.AZURE_OPENAI:
            missing = [
                name
                for name, val in (
                    ("azure_openai_api_key", settings.azure_openai_api_key),
                    ("azure_openai_endpoint", settings.azure_openai_endpoint),
                    ("azure_openai_deployment", settings.azure_openai_deployment),
                )
                if not val
            ]
            if missing:
                raise ConfigurationError(
                    f"Provider 'azure_openai' requires: {', '.join(missing)}."
                )
            return

        field_name, value = required[provider]
        if not value:
            raise ConfigurationError(
                f"Provider {provider.value!r} requires '{field_name}' to be set."
            )

    def build(self) -> BaseChatModel:
        """Instantiate the configured chat model.

        Imports are deferred so that only the active provider's SDK must be
        importable at runtime.

        Returns:
            A LangChain ``BaseChatModel`` ready for ``.with_structured_output``.

        Raises:
            ConfigurationError: If the provider is unrecognised.
        """
        s = self._settings
        provider = s.llm_provider
        logger.info("Building LLM client for provider=%s", provider.value)

        if provider in (LLMProvider.GROQ_FREE, LLMProvider.GROQ_PAID):
            from langchain_groq import ChatGroq

            # 70B (not 8B) for both tiers: the 8B model is unreliable at the
            # structured tool/function call the audit requires (`tool_use_failed`),
            # while token-per-minute limits are per-request, not per-model.
            model = "llama-3.3-70b-versatile"
            # langchain accepts `model` via field alias; its stubs name the field
            # `model_name`, so silence the version-dependent call-arg mismatch.
            return ChatGroq(  # type: ignore[call-arg]
                model=model,
                temperature=s.llm_temperature,
                api_key=_secret(s.groq_api_key),
                max_retries=0,  # retries handled centrally via tenacity in engine
            )

        if provider is LLMProvider.GEMINI_FREE:
            from langchain_google_genai import ChatGoogleGenerativeAI

            return ChatGoogleGenerativeAI(
                model="gemini-1.5-flash",
                temperature=s.llm_temperature,
                google_api_key=_secret(s.google_api_key),
            )

        if provider is LLMProvider.OPENAI:
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(
                model="gpt-4o-mini",
                temperature=s.llm_temperature,
                api_key=_secret(s.openai_api_key),
                max_retries=0,
            )

        if provider is LLMProvider.ANTHROPIC:
            from langchain_anthropic import ChatAnthropic

            return ChatAnthropic(  # type: ignore[call-arg]
                model_name="claude-haiku-4-5-20251001",
                temperature=s.llm_temperature,
                api_key=_secret(s.anthropic_api_key),
                max_retries=0,
                timeout=60,
                stop=None,
            )

        if provider is LLMProvider.AZURE_OPENAI:
            from langchain_openai import AzureChatOpenAI

            return AzureChatOpenAI(
                azure_deployment=s.azure_openai_deployment,
                azure_endpoint=s.azure_openai_endpoint,
                api_version=s.azure_openai_api_version,
                temperature=s.llm_temperature,
                api_key=_secret(s.azure_openai_api_key),
                max_retries=0,
            )

        raise ConfigurationError(f"Unsupported provider: {provider!r}")


def _secret(value: SecretStr | None) -> str | None:
    """Unwrap a :class:`SecretStr` for SDK calls, preserving ``None``."""
    return value.get_secret_value() if value is not None else None


def configure_logging(settings: Settings) -> None:
    """Configure root logging: rotating file + stdout, request-id aware.

    The ``request_id`` field is injected per-request by the API middleware
    (see ``apps/api/middleware/request_context.py``). A filter supplies a
    default so module-level logs (outside a request) still format cleanly.

    Args:
        settings: Loaded settings (provides ``log_dir`` and ``log_level``).
    """
    settings.log_dir.mkdir(parents=True, exist_ok=True)

    fmt = (
        "%(asctime)s | %(levelname)-8s | %(name)s | "
        "req=%(request_id)s | %(message)s"
    )
    formatter = logging.Formatter(fmt)

    class _RequestIdFilter(logging.Filter):
        """Guarantees a ``request_id`` attribute on every record."""

        def filter(self, record: logging.LogRecord) -> bool:
            if not hasattr(record, "request_id"):
                record.request_id = "-"
            return True

    file_handler = logging.handlers.RotatingFileHandler(
        settings.log_dir / "rag_core.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    # The filter MUST live on the handlers, not the logger: a logger-level filter
    # only sees records logged directly to "rag_core", not records propagated up
    # from child loggers (rag_core.engine, rag_core.api.*) or emitted on a
    # background thread. Handler-level filters see every record the handler
    # formats, guaranteeing `request_id` is always present.
    for handler in (file_handler, stream_handler):
        handler.addFilter(_RequestIdFilter())

    root = logging.getLogger("rag_core")
    root.setLevel(settings.log_level)
    # Idempotent: clear handlers so repeated calls (tests, reload) don't stack.
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(stream_handler)
    root.propagate = False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings, validating the provider gate once.

    Returns:
        The singleton :class:`Settings` instance.

    Raises:
        ConfigurationError: If the environment/provider pairing is unsafe.
    """
    settings = Settings()
    # Fail fast even if the factory is never explicitly constructed.
    LLMProviderFactory.validate_environment(settings)
    return settings
