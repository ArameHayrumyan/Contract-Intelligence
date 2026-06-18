"""rag_core — tenant-scoped contract intelligence & SLA auditing core.

This package holds all business logic and is consumed exclusively by the FastAPI
service. The public surface is re-exported here for convenient, stable imports.

Imports are resolved **lazily** (PEP 562): touching ``rag_core.config`` or
``rag_core.schemas`` does not drag in the heavy optional dependencies of
``storage`` (Chroma/torch) or ``processor`` (OpenCV/tesseract). The names below
resolve on first access exactly as if eagerly imported.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # Eager names for type checkers / IDEs only.
    from rag_core.config import (
        ConfigurationError,
        Environment,
        LLMProvider,
        LLMProviderFactory,
        Settings,
        configure_logging,
        get_settings,
    )
    from rag_core.engine import AuditEngine, reciprocal_rank_fusion
    from rag_core.ingestion_queue import IngestionQueue, InProcessIngestionQueue
    from rag_core.processor import DocumentProcessor, ProcessingResult
    from rag_core.schemas import (
        Chunk,
        ContractAuditSchema,
        CriticalClause,
        DocumentRecord,
        DocumentStatus,
        QARequest,
        QAResponse,
        RiskBand,
    )
    from rag_core.security import UploadValidationError, validate_upload
    from rag_core.storage import TenantVectorStore

#: Map of public name → submodule that defines it (for lazy resolution).
_EXPORTS: dict[str, str] = {
    "ConfigurationError": "config",
    "Environment": "config",
    "LLMProvider": "config",
    "LLMProviderFactory": "config",
    "Settings": "config",
    "configure_logging": "config",
    "get_settings": "config",
    "AuditEngine": "engine",
    "reciprocal_rank_fusion": "engine",
    "IngestionQueue": "ingestion_queue",
    "InProcessIngestionQueue": "ingestion_queue",
    "DocumentProcessor": "processor",
    "ProcessingResult": "processor",
    "Chunk": "schemas",
    "ContractAuditSchema": "schemas",
    "CriticalClause": "schemas",
    "DocumentRecord": "schemas",
    "DocumentStatus": "schemas",
    "QARequest": "schemas",
    "QAResponse": "schemas",
    "RiskBand": "schemas",
    "UploadValidationError": "security",
    "validate_upload": "security",
    "TenantVectorStore": "storage",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    """Lazily import and return a public symbol (PEP 562).

    Args:
        name: The attribute being accessed on the package.

    Returns:
        The resolved object.

    Raises:
        AttributeError: If ``name`` is not part of the public surface.
    """
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module 'rag_core' has no attribute {name!r}")
    import importlib

    module = importlib.import_module(f"rag_core.{module_name}")
    return getattr(module, name)


def __dir__() -> list[str]:
    """Expose the public surface to ``dir()`` and autocompletion."""
    return __all__
