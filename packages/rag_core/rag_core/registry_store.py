"""Durable document & standard registry (synchronous SQLAlchemy Core).

Why synchronous (unlike the async ``database.py`` audit store): these records
are written from **background ingestion threads** (the ThreadPoolExecutor in
``ContractService``). A sync engine is callable from any thread with no
async-from-thread bridging or event-loop deadlocks, and it keeps the service
methods sync so the FastAPI routers stay unchanged. Both modules point at the
same database (SQLite by default, PostgreSQL via ``DATABASE_URL``).

This replaces the previous in-memory dicts, so uploaded documents and standards
(and their ingestion status) survive an API restart — the Chroma vectors and the
audit results already persist on disk, and now the registry does too.

Every function is tenant-scoped: ``tenant_id`` is always required and always in
the WHERE clause.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Column,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    and_,
    create_engine,
    event,
    select,
)
from sqlalchemy.engine import Engine

metadata = MetaData()

documents = Table(
    "documents",
    metadata,
    Column("document_id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("filename", String, nullable=False),
    Column("status", String, nullable=False),
    Column("page_count", Integer),
    Column("chunk_count", Integer),
    Column("error", String),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Index("ix_documents_tenant", "tenant_id"),
)

standards = Table(
    "standards",
    metadata,
    Column("standard_document_id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("standard_name", String, nullable=False),
    Column("standard_version", String, nullable=False),
    Column("status", String, nullable=False),
    Column("chunk_count", Integer),
    Column("error", String),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Index("ix_standards_tenant", "tenant_id"),
)

_engine: Engine | None = None


def init_registry(url: str) -> None:
    """Create the sync engine and registry tables (idempotent).

    Args:
        url: A sync SQLAlchemy URL (``sqlite:///…`` or ``postgresql+psycopg://…``).
    """
    global _engine
    if _engine is not None:
        _engine.dispose()
    is_sqlite = url.startswith("sqlite")
    _engine = create_engine(
        url,
        future=True,
        pool_pre_ping=True,
        # SQLite connections are shared across the request + ingestion threads.
        connect_args={"check_same_thread": False} if is_sqlite else {},
    )
    if is_sqlite:
        # WAL lets readers and the single writer proceed concurrently, which the
        # two engines (this + the async audit store) on one file rely on.
        @event.listens_for(_engine, "connect")
        def _set_wal(dbapi_conn: Any, _record: Any) -> None:  # pragma: no cover
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    metadata.create_all(_engine)


def dispose_registry() -> None:
    """Dispose the engine (called on application shutdown)."""
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None


def _require() -> Engine:
    """Return the initialised engine or raise if startup was skipped."""
    if _engine is None:
        raise RuntimeError("Registry not initialised; call init_registry() at startup.")
    return _engine


def _now() -> str:
    """Current UTC time as an ISO 8601 string."""
    return datetime.now(UTC).isoformat()


# --- Documents ---------------------------------------------------------------


def insert_document(
    *,
    document_id: str,
    tenant_id: str,
    filename: str,
    status: str,
    page_count: int | None,
) -> None:
    """Insert a freshly registered document (status ``pending``)."""
    now = _now()
    with _require().begin() as conn:
        conn.execute(
            documents.insert().values(
                document_id=document_id,
                tenant_id=tenant_id,
                filename=filename,
                status=status,
                page_count=page_count,
                chunk_count=None,
                error=None,
                created_at=now,
                updated_at=now,
            )
        )


def update_document(
    *,
    tenant_id: str,
    document_id: str,
    status: str,
    chunk_count: int | None = None,
    error: str | None = None,
) -> None:
    """Update a document's ingestion status (and chunk count / error)."""
    values: dict[str, Any] = {"status": status, "error": error, "updated_at": _now()}
    if chunk_count is not None:
        values["chunk_count"] = chunk_count
    with _require().begin() as conn:
        conn.execute(
            documents.update()
            .where(
                and_(
                    documents.c.document_id == document_id,
                    documents.c.tenant_id == tenant_id,
                )
            )
            .values(**values)
        )


def get_document(tenant_id: str, document_id: str) -> dict[str, Any] | None:
    """Return one document row for a tenant, or ``None``."""
    query = select(documents).where(
        and_(
            documents.c.document_id == document_id,
            documents.c.tenant_id == tenant_id,
        )
    )
    with _require().connect() as conn:
        row = conn.execute(query).mappings().first()
    return dict(row) if row else None


# --- Standards ---------------------------------------------------------------


def insert_standard(
    *,
    standard_document_id: str,
    tenant_id: str,
    standard_name: str,
    standard_version: str,
    status: str,
) -> None:
    """Insert a freshly registered standard version (status ``pending``)."""
    now = _now()
    with _require().begin() as conn:
        conn.execute(
            standards.insert().values(
                standard_document_id=standard_document_id,
                tenant_id=tenant_id,
                standard_name=standard_name,
                standard_version=standard_version,
                status=status,
                chunk_count=None,
                error=None,
                created_at=now,
                updated_at=now,
            )
        )


def update_standard(
    *,
    tenant_id: str,
    standard_document_id: str,
    status: str,
    chunk_count: int | None = None,
    error: str | None = None,
) -> None:
    """Update a standard's ingestion status (and chunk count / error)."""
    values: dict[str, Any] = {"status": status, "error": error, "updated_at": _now()}
    if chunk_count is not None:
        values["chunk_count"] = chunk_count
    with _require().begin() as conn:
        conn.execute(
            standards.update()
            .where(
                and_(
                    standards.c.standard_document_id == standard_document_id,
                    standards.c.tenant_id == tenant_id,
                )
            )
            .values(**values)
        )


def get_standard(
    tenant_id: str, standard_document_id: str
) -> dict[str, Any] | None:
    """Return one standard row for a tenant, or ``None``."""
    query = select(standards).where(
        and_(
            standards.c.standard_document_id == standard_document_id,
            standards.c.tenant_id == tenant_id,
        )
    )
    with _require().connect() as conn:
        row = conn.execute(query).mappings().first()
    return dict(row) if row else None


def list_standards(tenant_id: str) -> list[dict[str, Any]]:
    """Return all of a tenant's standard versions, sorted by name then version."""
    query = (
        select(standards)
        .where(standards.c.tenant_id == tenant_id)
        .order_by(standards.c.standard_name, standards.c.standard_version)
    )
    with _require().connect() as conn:
        rows = conn.execute(query).mappings().all()
    return [dict(r) for r in rows]
