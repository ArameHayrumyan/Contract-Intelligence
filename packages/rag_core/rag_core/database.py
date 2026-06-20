"""Async audit-result persistence (SQLite via SQLAlchemy Core).

The audit endpoint used to re-run the LLM pipeline on every call, leaving no
history to power a dashboard, monitoring, or exports. This module is the durable
record of audits and cross-reference runs.

Deliberately **SQLAlchemy Core** (explicit `Table` definitions), not the ORM, so
the schema is readable in one place and the production move to PostgreSQL is a
connection-string change plus a dialect swap — nothing more (see
``docs/SCALING_PATH.md``). Every public function is tenant-scoped: ``tenant_id``
is always required and always part of the WHERE clause.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import (
    Boolean,
    Column,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    and_,
    func,
    select,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from rag_core.schemas import ContractAuditSchema
from rag_core.schemas_xref import CrossReferenceAuditSchema

logger = logging.getLogger("rag_core.database")

_VALID_STATUSES = frozenset(
    {"processing", "audited", "reviewed", "approved", "flagged"}
)
_DEFAULT_THRESHOLDS = [30, 60, 90]

metadata = MetaData()

audit_results = Table(
    "audit_results",
    metadata,
    Column("id", String, primary_key=True),  # == document_id
    Column("tenant_id", String, nullable=False),
    Column("vendor_name", String, nullable=False),
    Column("contract_type", String, nullable=False),
    Column("auto_renewal", Boolean, nullable=False),
    Column("notice_period_days", Integer),
    Column("contract_end_date", String),  # ISO 8601 date, nullable
    Column("liability_cap", String, nullable=False),
    Column("risk_score", Integer, nullable=False),
    Column("risk_rationale", String, nullable=False),
    Column("critical_clauses", String, nullable=False),  # JSON array
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("status", String, nullable=False, default="audited"),
    Column("status_note", String),
    Column("status_updated_at", String),
    Column("has_crossref", Boolean, nullable=False, default=False),
    Index("ix_audit_tenant_risk", "tenant_id", "risk_score"),
    Index("ix_audit_tenant_enddate", "tenant_id", "contract_end_date"),
    Index("ix_audit_tenant_autorenew", "tenant_id", "auto_renewal"),
)

crossref_results = Table(
    "crossref_results",
    metadata,
    Column("id", String, primary_key=True),  # crossref run UUID
    Column("tenant_id", String, nullable=False),
    Column("subject_document_id", String, nullable=False),
    Column("standard_document_id", String, nullable=False),
    Column("standard_version", String, nullable=False),
    Column("overall_risk_score", Integer, nullable=False),
    Column("executive_summary", String, nullable=False),
    Column("deviations", String, nullable=False),  # full schema JSON
    Column("created_at", String, nullable=False),
    Index("ix_crossref_subject", "subject_document_id"),
)

tenant_settings = Table(
    "tenant_settings",
    metadata,
    Column("tenant_id", String, primary_key=True),
    Column("renewal_thresholds", String, nullable=False),  # JSON array
    Column("updated_at", String, nullable=False),
)


@dataclass
class AuditFilters:
    """Filter / sort / page options for listing audit results.

    Attributes:
        risk_score_min: Inclusive lower bound on risk score.
        risk_score_max: Inclusive upper bound on risk score.
        contract_type: Exact contract-type match.
        auto_renewal: Filter by auto-renewal flag.
        status: Exact workflow-status match.
        sort_by: Column to sort by.
        sort_order: Sort direction.
        page: 1-based page number.
        page_size: Rows per page.
    """

    risk_score_min: int | None = None
    risk_score_max: int | None = None
    contract_type: str | None = None
    auto_renewal: bool | None = None
    status: str | None = None
    sort_by: Literal[
        "risk_score", "created_at", "vendor_name", "contract_end_date"
    ] = "created_at"
    sort_order: Literal["asc", "desc"] = "desc"
    page: int = 1
    page_size: int = 20


# --- Engine lifecycle --------------------------------------------------------

_engine: AsyncEngine | None = None


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(UTC).isoformat()


async def init_db(db_path: Path | str) -> None:
    """Create the async engine and tables (idempotent).

    Called from the API lifespan *and* from tests (which don't run the lifespan).
    Safe to call repeatedly.

    Args:
        db_path: Filesystem path to the SQLite database file.
    """
    global _engine
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if _engine is not None:
        await _engine.dispose()
    # as_posix() keeps the URL valid on Windows (backslashes would break it).
    _engine = create_async_engine(
        f"sqlite+aiosqlite:///{path.as_posix()}", future=True
    )
    async with _engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    logger.info("Audit database ready at %s", path)


async def dispose_db() -> None:
    """Dispose the engine (called on application shutdown)."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


def _require_engine() -> AsyncEngine:
    """Return the initialised engine or raise if startup was skipped.

    Returns:
        The active :class:`AsyncEngine`.

    Raises:
        RuntimeError: If :func:`init_db` has not been called.
    """
    if _engine is None:
        raise RuntimeError("Database not initialised; call init_db() at startup.")
    return _engine


# --- Audit results -----------------------------------------------------------


async def upsert_audit_result(
    result: ContractAuditSchema,
    document_id: str,
    tenant_id: str,
    contract_end_date: date | None,
) -> None:
    """Insert or update an audit result, preserving workflow status on update.

    Args:
        result: The structured audit.
        document_id: The audited document (primary key).
        tenant_id: Owning tenant.
        contract_end_date: Parsed end date (overrides the schema value if given).
    """
    end_date = contract_end_date or result.contract_end_date
    now = _now_iso()
    values = {
        "id": document_id,
        "tenant_id": tenant_id,
        "vendor_name": result.vendor_name,
        "contract_type": result.contract_type,
        "auto_renewal": result.auto_renewal,
        "notice_period_days": result.notice_period_days,
        "contract_end_date": end_date.isoformat() if end_date else None,
        "liability_cap": result.liability_cap_description,
        "risk_score": result.risk_score,
        "risk_rationale": result.risk_rationale,
        "critical_clauses": json.dumps(
            [c.model_dump() for c in result.critical_clauses]
        ),
        "created_at": now,
        "updated_at": now,
        "status": "audited",
    }
    statement = sqlite_insert(audit_results).values(**values)
    # On re-audit, refresh the audit fields but keep the human workflow status.
    statement = statement.on_conflict_do_update(
        index_elements=[audit_results.c.id],
        set_={
            "vendor_name": statement.excluded.vendor_name,
            "contract_type": statement.excluded.contract_type,
            "auto_renewal": statement.excluded.auto_renewal,
            "notice_period_days": statement.excluded.notice_period_days,
            "contract_end_date": statement.excluded.contract_end_date,
            "liability_cap": statement.excluded.liability_cap,
            "risk_score": statement.excluded.risk_score,
            "risk_rationale": statement.excluded.risk_rationale,
            "critical_clauses": statement.excluded.critical_clauses,
            "updated_at": statement.excluded.updated_at,
        },
    )
    async with _require_engine().begin() as conn:
        await conn.execute(statement)


async def get_audit_result(document_id: str, tenant_id: str) -> dict[str, Any] | None:
    """Fetch one audit result for a tenant.

    Args:
        document_id: The document id.
        tenant_id: Owning tenant.

    Returns:
        The row as a dict (clauses decoded), or ``None`` if not found.
    """
    query = select(audit_results).where(
        and_(
            audit_results.c.id == document_id,
            audit_results.c.tenant_id == tenant_id,
        )
    )
    async with _require_engine().connect() as conn:
        row = (await conn.execute(query)).mappings().first()
    return _decode_audit_row(row) if row else None


async def list_audit_results(
    tenant_id: str, filters: AuditFilters
) -> dict[str, Any]:
    """List a tenant's audit results with filtering, sorting and pagination.

    Args:
        tenant_id: Owning tenant.
        filters: Filter / sort / page options.

    Returns:
        ``{items: list[dict], total: int, page, page_size, total_pages}``.
    """
    conditions = [audit_results.c.tenant_id == tenant_id]
    if filters.risk_score_min is not None:
        conditions.append(audit_results.c.risk_score >= filters.risk_score_min)
    if filters.risk_score_max is not None:
        conditions.append(audit_results.c.risk_score <= filters.risk_score_max)
    if filters.contract_type:
        conditions.append(audit_results.c.contract_type == filters.contract_type)
    if filters.auto_renewal is not None:
        conditions.append(audit_results.c.auto_renewal == filters.auto_renewal)
    if filters.status:
        conditions.append(audit_results.c.status == filters.status)
    where = and_(*conditions)

    sort_column = audit_results.c[filters.sort_by]
    ordering = (
        sort_column.desc() if filters.sort_order == "desc" else sort_column.asc()
    )
    page = max(1, filters.page)
    page_size = max(1, filters.page_size)

    count_query = select(func.count()).select_from(audit_results).where(where)
    rows_query = (
        select(audit_results)
        .where(where)
        .order_by(ordering)
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    async with _require_engine().connect() as conn:
        total = (await conn.execute(count_query)).scalar_one()
        rows = (await conn.execute(rows_query)).mappings().all()

    items = [_decode_audit_row(r) for r in rows]
    total_pages = (total + page_size - 1) // page_size if total else 0
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


async def update_audit_status(
    document_id: str, tenant_id: str, status: str, note: str | None
) -> dict[str, Any] | None:
    """Update the workflow status (and optional note) of an audit row.

    Args:
        document_id: The document id.
        tenant_id: Owning tenant.
        status: New status (validated against the allowed set).
        note: Optional human annotation.

    Returns:
        The updated row, or ``None`` if it does not exist.

    Raises:
        ValueError: If ``status`` is not an allowed value.
    """
    if status not in _VALID_STATUSES:
        raise ValueError(f"Invalid status {status!r}; allowed: {sorted(_VALID_STATUSES)}")
    now = _now_iso()
    statement = (
        audit_results.update()
        .where(
            and_(
                audit_results.c.id == document_id,
                audit_results.c.tenant_id == tenant_id,
            )
        )
        .values(status=status, status_note=note, status_updated_at=now, updated_at=now)
    )
    async with _require_engine().begin() as conn:
        await conn.execute(statement)
    return await get_audit_result(document_id, tenant_id)


# --- Cross-reference results -------------------------------------------------


async def upsert_crossref_result(
    result: CrossReferenceAuditSchema, tenant_id: str
) -> None:
    """Persist a cross-reference run and flag its subject as having one.

    Args:
        result: The cross-reference audit.
        tenant_id: Owning tenant.
    """
    import uuid

    values = {
        "id": uuid.uuid4().hex,
        "tenant_id": tenant_id,
        "subject_document_id": result.subject_document_id,
        "standard_document_id": result.standard_document_id,
        "standard_version": result.standard_version,
        "overall_risk_score": result.overall_risk_score,
        "executive_summary": result.executive_summary,
        "deviations": result.model_dump_json(),
        "created_at": _now_iso(),
    }
    flag = (
        audit_results.update()
        .where(
            and_(
                audit_results.c.id == result.subject_document_id,
                audit_results.c.tenant_id == tenant_id,
            )
        )
        .values(has_crossref=True)
    )
    async with _require_engine().begin() as conn:
        await conn.execute(crossref_results.insert().values(**values))
        await conn.execute(flag)


async def get_crossref_by_subject(
    subject_document_id: str, tenant_id: str
) -> dict[str, Any] | None:
    """Fetch the latest cross-reference run for a subject document.

    Args:
        subject_document_id: The audited contract id.
        tenant_id: Owning tenant.

    Returns:
        The decoded row (with parsed ``deviations``), or ``None``.
    """
    query = (
        select(crossref_results)
        .where(
            and_(
                crossref_results.c.subject_document_id == subject_document_id,
                crossref_results.c.tenant_id == tenant_id,
            )
        )
        .order_by(crossref_results.c.created_at.desc())
        .limit(1)
    )
    async with _require_engine().connect() as conn:
        row = (await conn.execute(query)).mappings().first()
    if not row:
        return None
    data = dict(row)
    data["deviations"] = json.loads(data["deviations"])
    return data


# --- Renewal / monitoring ----------------------------------------------------


async def get_renewal_alerts(
    tenant_id: str, threshold_days: int
) -> list[dict[str, Any]]:
    """Return auto-renewing contracts expiring within ``threshold_days``.

    Args:
        tenant_id: Owning tenant.
        threshold_days: Days-ahead window (inclusive).

    Returns:
        Matching rows, soonest end-date first.
    """
    today = date.today()
    horizon = today + _days(threshold_days)
    query = (
        select(audit_results)
        .where(
            and_(
                audit_results.c.tenant_id == tenant_id,
                audit_results.c.auto_renewal.is_(True),
                audit_results.c.contract_end_date.is_not(None),
                audit_results.c.contract_end_date >= today.isoformat(),
                audit_results.c.contract_end_date <= horizon.isoformat(),
            )
        )
        .order_by(audit_results.c.contract_end_date.asc())
    )
    async with _require_engine().connect() as conn:
        rows = (await conn.execute(query)).mappings().all()
    return [_decode_audit_row(r) for r in rows]


async def get_unknown_date_autorenewals(tenant_id: str) -> list[dict[str, Any]]:
    """Return auto-renewing contracts with no recorded end date.

    Args:
        tenant_id: Owning tenant.

    Returns:
        Matching rows.
    """
    query = select(audit_results).where(
        and_(
            audit_results.c.tenant_id == tenant_id,
            audit_results.c.auto_renewal.is_(True),
            audit_results.c.contract_end_date.is_(None),
        )
    )
    async with _require_engine().connect() as conn:
        rows = (await conn.execute(query)).mappings().all()
    return [_decode_audit_row(r) for r in rows]


# --- Tenant settings (monitoring thresholds) ---------------------------------


async def get_renewal_thresholds(tenant_id: str) -> list[int]:
    """Return a tenant's configured renewal thresholds, or the defaults.

    Args:
        tenant_id: Owning tenant.

    Returns:
        Up to three ascending day-thresholds.
    """
    query = select(tenant_settings.c.renewal_thresholds).where(
        tenant_settings.c.tenant_id == tenant_id
    )
    async with _require_engine().connect() as conn:
        value = (await conn.execute(query)).scalar_one_or_none()
    if not value:
        return list(_DEFAULT_THRESHOLDS)
    decoded = json.loads(value)
    return [int(v) for v in decoded]


async def set_renewal_thresholds(tenant_id: str, thresholds: list[int]) -> list[int]:
    """Persist a tenant's renewal thresholds.

    Args:
        tenant_id: Owning tenant.
        thresholds: 1-3 strictly-ascending values in [1, 365].

    Returns:
        The saved thresholds.

    Raises:
        ValueError: If the thresholds fail validation.
    """
    if not 1 <= len(thresholds) <= 3:
        raise ValueError("Provide between 1 and 3 thresholds.")
    if any(not 1 <= t <= 365 for t in thresholds):
        raise ValueError("Each threshold must be between 1 and 365 days.")
    if any(a >= b for a, b in zip(thresholds, thresholds[1:], strict=False)):
        raise ValueError("Thresholds must be strictly ascending.")
    now = _now_iso()
    statement = sqlite_insert(tenant_settings).values(
        tenant_id=tenant_id,
        renewal_thresholds=json.dumps(thresholds),
        updated_at=now,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[tenant_settings.c.tenant_id],
        set_={
            "renewal_thresholds": statement.excluded.renewal_thresholds,
            "updated_at": statement.excluded.updated_at,
        },
    )
    async with _require_engine().begin() as conn:
        await conn.execute(statement)
    return thresholds


# --- Helpers -----------------------------------------------------------------


def _days(n: int) -> Any:
    """Return a ``timedelta`` of ``n`` days (kept local to avoid a top import)."""
    from datetime import timedelta

    return timedelta(days=n)


def _decode_audit_row(row: Any) -> dict[str, Any]:
    """Convert a result row mapping to a plain dict, decoding JSON fields.

    Args:
        row: A SQLAlchemy ``RowMapping``.

    Returns:
        A plain dict with ``critical_clauses`` decoded to a list.
    """
    data = dict(row)
    data["document_id"] = data["id"]  # consumer-friendly alias for the PK
    raw_clauses = data.get("critical_clauses")
    data["critical_clauses"] = json.loads(raw_clauses) if raw_clauses else []
    return data
