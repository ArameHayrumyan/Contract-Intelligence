"""PDF export endpoints (single document and full portfolio).

Exports are always generated from persisted audit data — never by re-running the
RAG engine — so they are fast, deterministic, and reflect the stored record.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse

from dependencies import TenantIdDep
from rag_core.database import (
    AuditFilters,
    get_audit_result,
    get_crossref_by_subject,
    list_audit_results,
)
from rag_core.report_generator import ReportGenerator
from rag_core.schemas import ContractAuditSchema, CriticalClause
from rag_core.schemas_xref import CrossReferenceAuditSchema
from routers.dashboard import get_summary

logger = logging.getLogger("rag_core.api.exports")

router = APIRouter(tags=["exports"])

_generator = ReportGenerator()


def _slug(value: str) -> str:
    """Make a filename-safe slug from a vendor name."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:40] or "contract"


def _row_to_audit(row: dict[str, Any]) -> ContractAuditSchema:
    """Reconstruct a :class:`ContractAuditSchema` from a persisted row."""
    end_raw = row.get("contract_end_date")
    return ContractAuditSchema(
        vendor_name=row["vendor_name"],
        contract_type=row["contract_type"],
        auto_renewal=bool(row["auto_renewal"]),
        notice_period_days=row.get("notice_period_days") or 0,
        liability_cap_description=row["liability_cap"],
        contract_end_date=date.fromisoformat(end_raw) if end_raw else None,
        risk_score=row["risk_score"],
        risk_rationale=row["risk_rationale"],
        critical_clauses=[CriticalClause(**c) for c in row.get("critical_clauses", [])],
    )


def _pdf_response(pdf: bytes, filename: str) -> StreamingResponse:
    """Wrap PDF bytes in a download response."""
    return StreamingResponse(
        io.BytesIO(pdf),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/documents/{document_id}/export/pdf")
async def export_document_pdf(
    document_id: str, tenant_id: TenantIdDep
) -> StreamingResponse:
    """Export a single contract's audit (and cross-reference, if any) as PDF.

    Args:
        document_id: The audited document.
        tenant_id: The caller's tenant.

    Returns:
        A streaming PDF download.

    Raises:
        HTTPException: 404 if no persisted audit exists for the document.
    """
    row = await get_audit_result(document_id, tenant_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No audit found for document {document_id}.",
        )

    crossref: CrossReferenceAuditSchema | None = None
    if row.get("has_crossref"):
        cr = await get_crossref_by_subject(document_id, tenant_id)
        if cr is not None:
            crossref = CrossReferenceAuditSchema.model_validate(cr["deviations"])

    pdf = _generator.generate_single_document_report(
        audit=_row_to_audit(row),
        document_id=document_id,
        crossref=crossref,
        tenant_id=tenant_id,
    )
    filename = f"audit_{_slug(row['vendor_name'])}_{date.today().isoformat()}.pdf"
    return _pdf_response(pdf, filename)


@router.get("/portfolio/export/pdf")
async def export_portfolio_pdf(tenant_id: TenantIdDep) -> StreamingResponse:
    """Export the full tenant portfolio as a PDF report.

    Args:
        tenant_id: The caller's tenant.

    Returns:
        A streaming PDF download.
    """
    data = await list_audit_results(tenant_id, AuditFilters(page_size=1000))
    summary = await get_summary(tenant_id)
    pdf = _generator.generate_portfolio_report(
        audits=data["items"],
        summary=summary,
        tenant_id=tenant_id,
        generated_at=datetime.now(UTC),
    )
    filename = f"portfolio_report_{date.today().isoformat()}.pdf"
    return _pdf_response(pdf, filename)
