"""Portfolio dashboard endpoints (read + workflow-status mutation).

All reporting is computed from the persisted ``audit_results`` table, never from
ChromaDB (the vector store is for retrieval, not analytics). Every query is
tenant-scoped via the auth-derived ``tenant_id``.
"""

from __future__ import annotations

import io
import logging
import zipfile
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from dependencies import ActorDep, TenantIdDep
from rag_core.database import (
    AuditFilters,
    bulk_update_status,
    get_audit_result,
    insert_activity,
    list_audit_results,
    update_audit_status,
)
from rag_core.schemas import (
    ActivityAction,
    BulkExportRequest,
    BulkStatusRequest,
    RiskBand,
)
from routers._reporting import build_single_report, slugify

logger = logging.getLogger("rag_core.api.dashboard")

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

#: Window (days) for the "expiring soon" stat card, independent of the
#: tenant-configurable monitoring thresholds.
_EXPIRING_SOON_DAYS = 60


class StatusUpdate(BaseModel):
    """Body for a workflow-status change.

    Attributes:
        status: New status (validated by the database layer).
        note: Optional human annotation.
    """

    status: str
    note: str | None = None


def _parse_iso_date(value: Any) -> date | None:
    """Parse an ISO date string to a ``date``, or ``None``."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


@router.get("/summary")
async def get_summary(tenant_id: TenantIdDep) -> dict[str, Any]:
    """Return portfolio-level statistics for the tenant.

    Args:
        tenant_id: The caller's tenant.

    Returns:
        Totals, average risk, risk distribution, auto-renewal and expiring-soon
        counts, and the five most recent high-risk contracts.
    """
    result = await list_audit_results(
        tenant_id, AuditFilters(page_size=100_000, sort_by="created_at", sort_order="desc")
    )
    items: list[dict[str, Any]] = result["items"]
    total = len(items)

    distribution = {"low": 0, "medium": 0, "high": 0}
    for item in items:
        distribution[RiskBand.from_score(item["risk_score"]).value] += 1

    avg = round(sum(i["risk_score"] for i in items) / total, 1) if total else 0.0
    today = date.today()
    expiring_soon = sum(
        1
        for i in items
        if (d := _parse_iso_date(i.get("contract_end_date"))) is not None
        and today <= d <= today + _days(_EXPIRING_SOON_DAYS)
    )
    recent_high_risk = [
        {
            "document_id": i["document_id"],
            "vendor_name": i["vendor_name"],
            "risk_score": i["risk_score"],
            "created_at": i["created_at"],
        }
        for i in items
        if i["risk_score"] >= 8
    ][:5]

    return {
        "total_contracts": total,
        "avg_risk_score": avg,
        "risk_distribution": distribution,
        "contracts_with_autorenewal": sum(1 for i in items if i["auto_renewal"]),
        "contracts_expiring_soon": expiring_soon,
        "recent_high_risk": recent_high_risk,
    }


@router.get("/contracts")
async def list_contracts(
    tenant_id: TenantIdDep,
    risk_score_min: int | None = Query(default=None, ge=1, le=10),
    risk_score_max: int | None = Query(default=None, ge=1, le=10),
    contract_type: str | None = None,
    auto_renewal: bool | None = None,
    document_status: str | None = Query(default=None, alias="status"),
    sort_by: str = "created_at",
    sort_order: str = "desc",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=1000),
) -> dict[str, Any]:
    """Return a filtered, sorted, paginated page of audit results.

    Args:
        tenant_id: The caller's tenant.
        risk_score_min: Inclusive minimum risk score.
        risk_score_max: Inclusive maximum risk score.
        contract_type: Exact contract-type filter.
        auto_renewal: Auto-renewal filter.
        document_status: Workflow-status filter (query param ``status``).
        sort_by: Sort column.
        sort_order: Sort direction.
        page: 1-based page number.
        page_size: Rows per page.

    Returns:
        ``{items, total, page, page_size, total_pages}``.
    """
    allowed_sort = {"risk_score", "created_at", "vendor_name", "contract_end_date"}
    filters = AuditFilters(
        risk_score_min=risk_score_min,
        risk_score_max=risk_score_max,
        contract_type=contract_type,
        auto_renewal=auto_renewal,
        status=document_status,
        sort_by=sort_by if sort_by in allowed_sort else "created_at",  # type: ignore[arg-type]
        sort_order="asc" if sort_order == "asc" else "desc",
        page=page,
        page_size=page_size,
    )
    return await list_audit_results(tenant_id, filters)


@router.patch("/contracts/{document_id}/status")
async def patch_status(
    document_id: str,
    payload: StatusUpdate,
    tenant_id: TenantIdDep,
    actor: ActorDep,
) -> dict[str, Any]:
    """Update a contract's workflow status.

    Args:
        document_id: The contract to update.
        payload: New status and optional note.
        tenant_id: The caller's tenant.

    Returns:
        The updated audit record.

    Raises:
        HTTPException: 422 for an invalid status, 404 if the contract is unknown.
    """
    try:
        record = await update_audit_status(
            document_id, tenant_id, payload.status, payload.note, actor
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Contract {document_id} not found.",
        )
    return record


async def _assert_all_owned(document_ids: list[str], tenant_id: str) -> None:
    """Reject the whole batch (403) if any id is not the tenant's.

    Args:
        document_ids: Ids to validate.
        tenant_id: The caller's tenant.

    Raises:
        HTTPException: 403 listing the invalid ids.
    """
    invalid = [
        d for d in document_ids if await get_audit_result(d, tenant_id) is None
    ]
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"message": "Unknown document ids for this tenant.", "invalid": invalid},
        )


@router.post("/contracts/bulk/status")
async def bulk_status(
    payload: BulkStatusRequest, tenant_id: TenantIdDep, actor: ActorDep
) -> dict[str, Any]:
    """Bulk-update workflow status; all-or-nothing on ownership.

    Args:
        payload: Document ids + new status (+ optional note).
        tenant_id: The caller's tenant.
        actor: Who performed the action.

    Returns:
        ``{updated: int, failed: list[str]}``.
    """
    await _assert_all_owned(payload.document_ids, tenant_id)
    return await bulk_update_status(
        payload.document_ids, tenant_id, payload.status, payload.note, actor
    )


@router.post("/contracts/bulk/export")
async def bulk_export(
    payload: BulkExportRequest, tenant_id: TenantIdDep, actor: ActorDep
) -> StreamingResponse:
    """Export many contracts as a single in-memory zip of PDFs.

    A per-document failure does not abort the zip — failures are collected into
    an ``ERRORS.txt`` entry instead.

    Args:
        payload: Document ids to export (max 20).
        tenant_id: The caller's tenant.
        actor: Who performed the export.

    Returns:
        A streaming ``application/zip`` download.
    """
    await _assert_all_owned(payload.document_ids, tenant_id)
    buffer = io.BytesIO()
    errors: list[str] = []
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for document_id in payload.document_ids:
            try:
                built = await build_single_report(tenant_id, document_id)
                if built is None:
                    errors.append(f"{document_id}: no audit found")
                    continue
                pdf, vendor = built
                name = f"audit_{slugify(vendor)}_{document_id[:8]}.pdf"
                archive.writestr(name, pdf)
            except Exception as exc:  # noqa: BLE001 - one failure must not abort
                logger.exception("Bulk export failed for %s", document_id)
                errors.append(f"{document_id}: {exc}")
        if errors:
            archive.writestr("ERRORS.txt", "\n".join(errors))

    await insert_activity(
        tenant_id=tenant_id,
        actor=actor,
        action=ActivityAction.BULK_EXPORTED,
        metadata={"count": len(payload.document_ids), "document_ids": payload.document_ids},
    )
    buffer.seek(0)
    filename = (
        f"bulk_export_{date.today().isoformat()}_"
        f"{len(payload.document_ids)}_contracts.zip"
    )
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _days(n: int) -> Any:
    """Return a ``timedelta`` of ``n`` days."""
    from datetime import timedelta

    return timedelta(days=n)
