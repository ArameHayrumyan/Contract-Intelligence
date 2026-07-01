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
import uuid
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
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    create_async_engine,
)

from rag_core.schemas import ActivityAction, ContractAuditSchema
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

# NB: the Python variable is ``annotations_table`` (not ``annotations``) because
# ``from __future__ import annotations`` already binds the name ``annotations``;
# the SQL table is still called "annotations".
annotations_table = Table(
    "annotations",
    metadata,
    Column("id", String, primary_key=True),  # UUID v4
    Column("tenant_id", String, nullable=False),
    Column("document_id", String, nullable=False),  # FK -> audit_results.id
    Column("target_type", String, nullable=False),  # document | clause | deviation
    Column("target_reference", String),  # chunk_id / deviation_id / NULL
    Column("annotation_type", String, nullable=False),
    Column("note", String, nullable=False),
    Column("actor", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("deleted_at", String),  # soft delete; non-null = hidden
    Index("ix_annotations_doc", "tenant_id", "document_id"),
    Index("ix_annotations_target", "tenant_id", "target_type", "target_reference"),
)

# COMPLIANCE: activity_log is APPEND-ONLY by design. There is intentionally NO
# update_* or delete_* function for this table — the only writer is
# insert_activity / _log. Any future code that issues UPDATE or DELETE against
# activity_log must be rejected in code review (see test_activity.py, which
# statically asserts no such SQL exists). At scale this becomes a write-once
# store (CloudTrail / Postgres trigger) — see docs/SCALING_PATH.md.
activity_log = Table(
    "activity_log",
    metadata,
    Column("id", String, primary_key=True),  # UUID v4
    Column("tenant_id", String, nullable=False),
    Column("document_id", String),  # NULL for tenant-level actions
    Column("actor", String, nullable=False),
    Column("action", String, nullable=False),
    Column("target_type", String),  # document | clause | deviation | NULL
    Column("target_reference", String),  # chunk_id / deviation_id / NULL
    Column("from_value", String),  # JSON, nullable
    Column("to_value", String),  # JSON, nullable
    Column("metadata", String),  # JSON, nullable
    Column("created_at", String, nullable=False),  # primary sort field
    Index("ix_activity_tenant_time", "tenant_id", "created_at"),
    Index("ix_activity_doc_time", "tenant_id", "document_id", "created_at"),
)

# Normalized cross-reference deviations: stable per-deviation ids so annotations
# can target a specific deviation. Written alongside the crossref_results JSON
# blob (which is unchanged) at crossref time.
crossref_deviations = Table(
    "crossref_deviations",
    metadata,
    Column("id", String, primary_key=True),  # UUID v4, generated at crossref time
    Column("crossref_id", String, nullable=False),  # FK -> crossref_results.id
    Column("tenant_id", String, nullable=False),
    Column("subject_document_id", String, nullable=False),
    Column("clause_type", String, nullable=False),
    Column("deviation_type", String, nullable=False),
    Column("severity", Integer, nullable=False),
    Column("subject_text", String, nullable=False),
    Column("subject_chunk_id", String, nullable=False),
    Column("subject_page", Integer),
    Column("standard_text", String),
    Column("standard_chunk_id", String),
    Column("standard_page", Integer),
    Column("explanation", String, nullable=False),
    Column("created_at", String, nullable=False),
    Index("ix_xrefdev_crossref", "crossref_id"),
    Index("ix_xrefdev_subject", "tenant_id", "subject_document_id"),
    Index("ix_xrefdev_type_sev", "tenant_id", "deviation_type", "severity"),
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


async def init_db(target: Path | str) -> None:
    """Create the async engine and tables (idempotent).

    Called from the API lifespan *and* from tests (which don't run the lifespan).
    Safe to call repeatedly.

    Args:
        target: Either an async SQLAlchemy URL (anything containing ``://``,
            e.g. ``sqlite+aiosqlite:///…`` or ``postgresql+asyncpg://…``) or a
            filesystem path to a SQLite file (back-compat for tests).
    """
    global _engine
    text = str(target)
    if "://" in text:
        url = text
    else:
        # as_posix() keeps the URL valid on Windows (backslashes would break it).
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite+aiosqlite:///{path.as_posix()}"
    if _engine is not None:
        await _engine.dispose()
    _engine = create_async_engine(url, future=True)
    async with _engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    logger.info("Audit database ready (%s)", url.split("@")[-1])


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


# --- Activity log (append-only) ----------------------------------------------


async def _log(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    actor: str,
    action: ActivityAction | str,
    document_id: str | None = None,
    target_type: str | None = None,
    target_reference: str | None = None,
    from_value: dict[str, Any] | None = None,
    to_value: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Write one activity-log row **within an existing transaction**.

    Mutation functions call this on the same connection as their write, so the
    mutation and its audit record commit atomically — the log can never be
    silently skipped.

    Args:
        conn: The open transaction connection.
        tenant_id: Owning tenant.
        actor: Who performed the action.
        action: The :class:`ActivityAction`.
        document_id: Affected document, if any.
        target_type: ``document`` / ``clause`` / ``deviation`` / ``None``.
        target_reference: chunk_id / deviation_id / ``None``.
        from_value: Previous state (JSON-encoded).
        to_value: New state (JSON-encoded).
        metadata: Extra context (JSON-encoded).
    """
    await conn.execute(
        activity_log.insert().values(
            id=uuid.uuid4().hex,
            tenant_id=tenant_id,
            document_id=document_id,
            actor=actor,
            action=str(action),
            target_type=target_type,
            target_reference=target_reference,
            from_value=json.dumps(from_value) if from_value is not None else None,
            to_value=json.dumps(to_value) if to_value is not None else None,
            metadata=json.dumps(metadata) if metadata is not None else None,
            created_at=_now_iso(),
        )
    )


async def insert_activity(
    tenant_id: str,
    actor: str,
    action: ActivityAction | str,
    document_id: str | None = None,
    target_type: str | None = None,
    target_reference: str | None = None,
    from_value: dict[str, Any] | None = None,
    to_value: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append one activity-log entry in its own transaction.

    Used by the router layer (e.g. exports) where the action does not happen
    inside a database mutation. There is intentionally no update/delete
    counterpart — the log is append-only.

    Args: see :func:`_log`.
    """
    async with _require_engine().begin() as conn:
        await _log(
            conn,
            tenant_id=tenant_id,
            actor=actor,
            action=action,
            document_id=document_id,
            target_type=target_type,
            target_reference=target_reference,
            from_value=from_value,
            to_value=to_value,
            metadata=metadata,
        )


async def list_activity(
    tenant_id: str,
    document_id: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    """Return the tenant's activity log, newest first, paginated.

    Args:
        tenant_id: Owning tenant.
        document_id: Restrict to one document when given.
        page: 1-based page number.
        page_size: Rows per page.

    Returns:
        ``{items, total, page, page_size, total_pages}``.
    """
    conditions = [activity_log.c.tenant_id == tenant_id]
    if document_id is not None:
        conditions.append(activity_log.c.document_id == document_id)
    where = and_(*conditions)
    page = max(1, page)
    page_size = max(1, page_size)

    count_query = select(func.count()).select_from(activity_log).where(where)
    rows_query = (
        select(activity_log)
        .where(where)
        .order_by(activity_log.c.created_at.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    async with _require_engine().connect() as conn:
        total = (await conn.execute(count_query)).scalar_one()
        rows = (await conn.execute(rows_query)).mappings().all()
    items = [_decode_activity_row(r) for r in rows]
    total_pages = (total + page_size - 1) // page_size if total else 0
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


# --- Audit results -----------------------------------------------------------


async def upsert_audit_result(
    result: ContractAuditSchema,
    document_id: str,
    tenant_id: str,
    contract_end_date: date | None,
    actor: str = "system",
) -> None:
    """Insert or update an audit result, preserving workflow status on update.

    Also appends an ``AUDIT_RUN`` activity-log entry in the same transaction.

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
        await _log(
            conn,
            tenant_id=tenant_id,
            actor=actor,
            action=ActivityAction.AUDIT_RUN,
            document_id=document_id,
            target_type="document",
            to_value={"risk_score": result.risk_score},
        )


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
    document_id: str,
    tenant_id: str,
    status: str,
    note: str | None,
    actor: str = "system",
) -> dict[str, Any] | None:
    """Update the workflow status (and optional note) of an audit row.

    Logs a ``STATUS_CHANGED`` activity entry (with from/to status) atomically.

    Args:
        document_id: The document id.
        tenant_id: Owning tenant.
        status: New status (validated against the allowed set).
        note: Optional human annotation.
        actor: Who changed the status.

    Returns:
        The updated row, or ``None`` if it does not exist.

    Raises:
        ValueError: If ``status`` is not an allowed value.
    """
    if status not in _VALID_STATUSES:
        raise ValueError(f"Invalid status {status!r}; allowed: {sorted(_VALID_STATUSES)}")
    existing = await get_audit_result(document_id, tenant_id)
    if existing is None:
        return None
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
        await _log(
            conn,
            tenant_id=tenant_id,
            actor=actor,
            action=ActivityAction.STATUS_CHANGED,
            document_id=document_id,
            target_type="document",
            from_value={"status": existing["status"]},
            to_value={"status": status, "note": note},
        )
    return await get_audit_result(document_id, tenant_id)


# --- Cross-reference results -------------------------------------------------


async def upsert_crossref_result(
    result: CrossReferenceAuditSchema, tenant_id: str, actor: str = "system"
) -> None:
    """Persist a cross-reference run and flag its subject as having one.

    Each deviation gets a stable ``deviation_id`` (so annotations can target it):
    the id is set on the in-memory object, embedded in the stored JSON blob
    (preserving order), and written as a normalized ``crossref_deviations`` row.
    Logs a ``CROSSREF_RUN`` activity entry. All in one transaction.

    Args:
        result: The cross-reference audit (mutated to carry deviation ids).
        tenant_id: Owning tenant.
        actor: Who ran the cross-reference.
    """
    crossref_id = uuid.uuid4().hex
    now = _now_iso()
    for deviation in result.deviations:
        deviation.deviation_id = uuid.uuid4().hex

    values = {
        "id": crossref_id,
        "tenant_id": tenant_id,
        "subject_document_id": result.subject_document_id,
        "standard_document_id": result.standard_document_id,
        "standard_version": result.standard_version,
        "overall_risk_score": result.overall_risk_score,
        "executive_summary": result.executive_summary,
        "deviations": result.model_dump_json(),  # blob now carries deviation_ids
        "created_at": now,
    }
    deviation_rows = [
        {
            "id": d.deviation_id,
            "crossref_id": crossref_id,
            "tenant_id": tenant_id,
            "subject_document_id": result.subject_document_id,
            "clause_type": d.clause_type,
            "deviation_type": str(d.deviation_type),
            "severity": d.severity,
            "subject_text": d.subject_text,
            "subject_chunk_id": d.subject_chunk_id,
            "subject_page": d.subject_page,
            "standard_text": d.standard_text,
            "standard_chunk_id": d.standard_chunk_id,
            "standard_page": d.standard_page,
            "explanation": d.explanation,
            "created_at": now,
        }
        for d in result.deviations
    ]
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
        if deviation_rows:
            await conn.execute(crossref_deviations.insert(), deviation_rows)
        await conn.execute(flag)
        await _log(
            conn,
            tenant_id=tenant_id,
            actor=actor,
            action=ActivityAction.CROSSREF_RUN,
            document_id=result.subject_document_id,
            target_type="document",
            to_value={"overall_risk_score": result.overall_risk_score},
        )


async def deviation_exists(
    tenant_id: str, subject_document_id: str, deviation_id: str
) -> bool:
    """Whether a deviation id exists for a tenant's document (annotation guard).

    Args:
        tenant_id: Owning tenant.
        subject_document_id: The audited contract id.
        deviation_id: The deviation id to verify.

    Returns:
        ``True`` if the deviation belongs to this tenant + document.
    """
    query = select(func.count()).select_from(crossref_deviations).where(
        and_(
            crossref_deviations.c.id == deviation_id,
            crossref_deviations.c.tenant_id == tenant_id,
            crossref_deviations.c.subject_document_id == subject_document_id,
        )
    )
    async with _require_engine().connect() as conn:
        return bool((await conn.execute(query)).scalar_one())


# --- Annotations -------------------------------------------------------------


async def _get_annotation(
    annotation_id: str, tenant_id: str
) -> dict[str, Any] | None:
    """Fetch a non-deleted annotation by id for a tenant, or ``None``."""
    query = select(annotations_table).where(
        and_(
            annotations_table.c.id == annotation_id,
            annotations_table.c.tenant_id == tenant_id,
            annotations_table.c.deleted_at.is_(None),
        )
    )
    async with _require_engine().connect() as conn:
        row = (await conn.execute(query)).mappings().first()
    return dict(row) if row else None


async def create_annotation(
    tenant_id: str,
    document_id: str,
    target_type: str,
    target_reference: str | None,
    annotation_type: str,
    note: str,
    actor: str,
) -> dict[str, Any]:
    """Create a human annotation and log it atomically.

    Args:
        tenant_id: Owning tenant.
        document_id: Annotated document.
        target_type: ``document`` / ``clause`` / ``deviation``.
        target_reference: chunk_id / deviation_id / ``None``.
        annotation_type: Reviewer classification.
        note: Note text.
        actor: Who recorded the note.

    Returns:
        The created annotation row.
    """
    now = _now_iso()
    values = {
        "id": uuid.uuid4().hex,
        "tenant_id": tenant_id,
        "document_id": document_id,
        "target_type": target_type,
        "target_reference": target_reference,
        "annotation_type": annotation_type,
        "note": note,
        "actor": actor,
        "created_at": now,
        "updated_at": now,
        "deleted_at": None,
    }
    async with _require_engine().begin() as conn:
        await conn.execute(annotations_table.insert().values(**values))
        await _log(
            conn,
            tenant_id=tenant_id,
            actor=actor,
            action=ActivityAction.ANNOTATION_ADDED,
            document_id=document_id,
            target_type=target_type,
            target_reference=target_reference,
            to_value={"note": note, "type": annotation_type},
        )
    return values


async def list_annotations(
    tenant_id: str,
    document_id: str,
    target_type: str | None = None,
    target_reference: str | None = None,
) -> list[dict[str, Any]]:
    """List non-deleted annotations for a document, newest first.

    Args:
        tenant_id: Owning tenant.
        document_id: The document.
        target_type: Optional filter.
        target_reference: Optional filter.

    Returns:
        Matching annotation rows (soft-deleted excluded).
    """
    conditions = [
        annotations_table.c.tenant_id == tenant_id,
        annotations_table.c.document_id == document_id,
        annotations_table.c.deleted_at.is_(None),
    ]
    if target_type:
        conditions.append(annotations_table.c.target_type == target_type)
    if target_reference:
        conditions.append(annotations_table.c.target_reference == target_reference)
    query = (
        select(annotations_table)
        .where(and_(*conditions))
        .order_by(annotations_table.c.created_at.desc())
    )
    async with _require_engine().connect() as conn:
        rows = (await conn.execute(query)).mappings().all()
    return [dict(r) for r in rows]


async def update_annotation(
    annotation_id: str,
    tenant_id: str,
    note: str,
    annotation_type: str,
    actor: str,
) -> dict[str, Any] | None:
    """Edit an annotation's note/type and log the change atomically.

    Args:
        annotation_id: The annotation to edit.
        tenant_id: Owning tenant.
        note: New note text.
        annotation_type: New classification.
        actor: Who edited.

    Returns:
        The updated annotation, or ``None`` if not found.
    """
    existing = await _get_annotation(annotation_id, tenant_id)
    if existing is None:
        return None
    now = _now_iso()
    statement = (
        annotations_table.update()
        .where(
            and_(
                annotations_table.c.id == annotation_id,
                annotations_table.c.tenant_id == tenant_id,
            )
        )
        .values(note=note, annotation_type=annotation_type, updated_at=now)
    )
    async with _require_engine().begin() as conn:
        await conn.execute(statement)
        await _log(
            conn,
            tenant_id=tenant_id,
            actor=actor,
            action=ActivityAction.ANNOTATION_UPDATED,
            document_id=existing["document_id"],
            target_type=existing["target_type"],
            target_reference=existing["target_reference"],
            from_value={"note": existing["note"], "type": existing["annotation_type"]},
            to_value={"note": note, "type": annotation_type},
        )
    return await _get_annotation(annotation_id, tenant_id)


async def delete_annotation(
    annotation_id: str, tenant_id: str, actor: str
) -> bool:
    """Soft-delete an annotation (sets ``deleted_at``) and log it.

    Hard delete is intentionally not implemented — the deletion itself is
    recorded in the append-only activity log.

    Args:
        annotation_id: The annotation to delete.
        tenant_id: Owning tenant.
        actor: Who deleted it.

    Returns:
        ``True`` if an annotation was deleted, ``False`` if not found.
    """
    existing = await _get_annotation(annotation_id, tenant_id)
    if existing is None:
        return False
    now = _now_iso()
    statement = (
        annotations_table.update()
        .where(
            and_(
                annotations_table.c.id == annotation_id,
                annotations_table.c.tenant_id == tenant_id,
            )
        )
        .values(deleted_at=now, updated_at=now)
    )
    async with _require_engine().begin() as conn:
        await conn.execute(statement)
        await _log(
            conn,
            tenant_id=tenant_id,
            actor=actor,
            action=ActivityAction.ANNOTATION_DELETED,
            document_id=existing["document_id"],
            target_type=existing["target_type"],
            target_reference=existing["target_reference"],
            from_value={"note": existing["note"], "type": existing["annotation_type"]},
        )
    return True


# --- Bulk operations ---------------------------------------------------------


async def bulk_update_status(
    document_ids: list[str],
    tenant_id: str,
    status: str,
    note: str | None,
    actor: str,
) -> dict[str, Any]:
    """Change the workflow status of many contracts in one transaction.

    Per-document status changes are rolled into a single ``BULK_STATUS_CHANGED``
    activity entry (the document_ids and count are in its metadata).

    Args:
        document_ids: Documents to update.
        tenant_id: Owning tenant.
        status: New status.
        note: Optional shared note.
        actor: Who performed the bulk action.

    Returns:
        ``{updated: int, failed: list[str]}``.

    Raises:
        ValueError: If ``status`` is invalid.
    """
    if status not in _VALID_STATUSES:
        raise ValueError(f"Invalid status {status!r}; allowed: {sorted(_VALID_STATUSES)}")
    now = _now_iso()
    updated = 0
    failed: list[str] = []
    async with _require_engine().begin() as conn:
        for document_id in document_ids:
            result = await conn.execute(
                audit_results.update()
                .where(
                    and_(
                        audit_results.c.id == document_id,
                        audit_results.c.tenant_id == tenant_id,
                    )
                )
                .values(
                    status=status,
                    status_note=note,
                    status_updated_at=now,
                    updated_at=now,
                )
            )
            if result.rowcount:
                updated += 1
            else:
                failed.append(document_id)
        await _log(
            conn,
            tenant_id=tenant_id,
            actor=actor,
            action=ActivityAction.BULK_STATUS_CHANGED,
            to_value={"status": status},
            metadata={"count": updated, "document_ids": document_ids},
        )
    return {"updated": updated, "failed": failed}


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


def _decode_activity_row(row: Any) -> dict[str, Any]:
    """Convert an activity-log row to a dict, decoding its JSON fields.

    Args:
        row: A SQLAlchemy ``RowMapping``.

    Returns:
        A plain dict with ``from_value`` / ``to_value`` / ``metadata`` decoded.
    """
    data = dict(row)
    for field in ("from_value", "to_value", "metadata"):
        raw = data.get(field)
        data[field] = json.loads(raw) if raw else None
    return data
