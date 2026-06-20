"""Shared pytest fixtures.

The fixtures wire a fully in-memory stack: a fake LLM-backed engine and a fake
vector store, so the API and engine can be exercised without network access,
provider credentials, or downloading the embedding model. Ingestion runs
synchronously for deterministic assertions.
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path
from typing import Any

import pytest

# --- Path wiring: make rag_core and the API importable ----------------------
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "packages" / "rag_core"))
sys.path.insert(0, str(_REPO_ROOT / "apps" / "api"))

# --- Base environment: dev + a free provider with a dummy key ---------------
os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("LLM_PROVIDER", "groq_free")
os.environ.setdefault("GROQ_API_KEY", "test-key")
os.environ.setdefault("ACCESS_CODE", "test-code")

from rag_core.config import Settings, get_settings  # noqa: E402
from rag_core.schemas import (  # noqa: E402
    Chunk,
    ContractAuditSchema,
    CriticalClause,
    QACitation,
    QAResponse,
)


def make_pdf(pages: int = 1) -> bytes:
    """Build a minimal valid multi-page PDF for upload tests.

    Args:
        pages: Number of blank pages.

    Returns:
        Valid PDF bytes (correct magic bytes + parseable by pypdf).
    """
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


class FakeStore:
    """In-memory stand-in for ``TenantVectorStore`` (no embedding model)."""

    def __init__(self) -> None:
        self.added: dict[tuple[str, str], list[Chunk]] = {}

    def add_chunks(self, tenant_id: str, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        key = (tenant_id, chunks[0].document_id)
        self.added.setdefault(key, []).extend(chunks)

    def query(self, *args: Any, **kwargs: Any) -> list[Any]:  # pragma: no cover
        return []

    def bm25_query(self, *args: Any, **kwargs: Any) -> list[Any]:  # pragma: no cover
        return []

    def delete_document(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        return None

    # Standards surface (cross-reference workflow) — no-ops for unit tests.
    def add_standard_chunks(self, tenant_id: str, chunks: list[Chunk], **_: Any) -> None:
        if not chunks:
            return
        self.added.setdefault((tenant_id, chunks[0].document_id), []).extend(chunks)

    def get_standard_version(self, *args: Any, **kwargs: Any) -> str:  # pragma: no cover
        return ""

    def get_document_chunks(self, *args: Any, **kwargs: Any) -> list[Any]:  # pragma: no cover
        return []

    def get_element_metadata(self, *args: Any, **kwargs: Any) -> dict[str, Any]:  # pragma: no cover
        return {"element_type": "text", "column_headers": [], "structured_data": []}

    def query_standards(self, *args: Any, **kwargs: Any) -> list[Any]:  # pragma: no cover
        return []

    def bm25_query_standards(self, *args: Any, **kwargs: Any) -> list[Any]:  # pragma: no cover
        return []


class FakeXrefEngine:
    """Deterministic cross-reference engine returning a canned result."""

    async def run(
        self, subject_document_id: str, standard_document_id: str, tenant_id: str
    ) -> Any:
        from rag_core.schemas_xref import CrossReferenceAuditSchema

        return CrossReferenceAuditSchema(
            subject_document_id=subject_document_id,
            standard_document_id=standard_document_id,
            standard_version="v1",
            deviations=[],
            overall_risk_score=1,
            executive_summary="No deviations.",
            tenant_id=tenant_id,
        )


class FakeEngine:
    """Deterministic engine returning canned audit / QA results."""

    def audit_document(self, *, tenant_id: str, document_id: str) -> ContractAuditSchema:
        return ContractAuditSchema(
            vendor_name="Acme Corp",
            contract_type="MSA",
            auto_renewal=True,
            notice_period_days=30,
            liability_cap_description="Capped at 12 months of fees.",
            risk_score=7,
            risk_rationale="Auto-renewal with a short notice window.",
            critical_clauses=[
                CriticalClause(
                    text="This agreement renews automatically for successive terms.",
                    source_chunk_id="chunk-1",
                    page_number=2,
                    category="termination",
                )
            ],
        )

    def answer_question(
        self,
        *,
        tenant_id: str,
        question: str,
        document_ids: list[str] | None = None,
    ) -> QAResponse:
        return QAResponse(
            answer=f"Answer to: {question}",
            citations=[
                QACitation(
                    chunk_id="chunk-1",
                    document_id="doc-1",
                    page_number=1,
                    snippet="Relevant clause text.",
                )
            ],
        )


@pytest.fixture()
def settings() -> Settings:
    """Return the cached settings (test env applied at import)."""
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture()
def service(settings: Settings, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Build a synchronous ContractService with fakes injected."""
    from service import ContractService

    svc = ContractService(
        settings=settings,
        store=FakeStore(),  # type: ignore[arg-type]
        engine=FakeEngine(),  # type: ignore[arg-type]
        xref_engine=FakeXrefEngine(),  # type: ignore[arg-type]
        synchronous=True,
    )

    # Bypass the real tiered parser (camelot/pymupdf, temp files) with a
    # deterministic chunk so ingestion tests don't need PDF parsers installed.
    def fake_parse_to_chunks(
        data: bytes, document_id: str, tenant_id: str
    ) -> list[Chunk]:
        return [
            Chunk(
                chunk_id="chunk-1",
                document_id=document_id,
                tenant_id=tenant_id,
                page_number=2,
                text="This agreement renews automatically.",
            )
        ]

    monkeypatch.setattr(svc, "_parse_to_chunks", fake_parse_to_chunks)
    return svc


@pytest.fixture()
def app(service):  # type: ignore[no-untyped-def]
    """Create the FastAPI app with the test service injected."""
    from main import create_app
    from runtime import get_service

    application = create_app()
    application.state.service = service
    application.dependency_overrides[get_service] = lambda: service
    return application


@pytest.fixture()
async def client(app):  # type: ignore[no-untyped-def]
    """Async HTTP client bound to the app (lifespan not run)."""
    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-API-Key": "demo-key-tenant-acme"},
    ) as ac:
        yield ac


@pytest.fixture()
async def audit_db(tmp_path):  # type: ignore[no-untyped-def]
    """Initialise a fresh temp audit database (the lifespan does not run in tests)."""
    from rag_core.database import dispose_db, init_db

    await init_db(tmp_path / "audit_test.db")
    yield
    await dispose_db()
