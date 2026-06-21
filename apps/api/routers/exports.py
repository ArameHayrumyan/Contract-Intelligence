"""PDF export endpoints (single document and full portfolio).

Exports are always generated from persisted audit data — never by re-running the
RAG engine — so they are fast, deterministic, and reflect the stored record.
Each export appends an entry to the immutable activity log.
"""

from __future__ import annotations

import io
import logging
from datetime import UTC, date, datetime

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse

from dependencies import ActorDep, TenantIdDep
from rag_core.database import AuditFilters, insert_activity, list_audit_results
from rag_core.report_generator import ReportGenerator
from rag_core.schemas import ActivityAction
from routers._reporting import build_single_report, slugify
from routers.dashboard import get_summary

logger = logging.getLogger("rag_core.api.exports")

router = APIRouter(tags=["exports"])

_generator = ReportGenerator()


def _pdf_response(pdf: bytes, filename: str) -> StreamingResponse:
    """Wrap PDF bytes in a download response."""
    return StreamingResponse(
        io.BytesIO(pdf),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/documents/{document_id}/export/pdf")
async def export_document_pdf(
    document_id: str, tenant_id: TenantIdDep, actor: ActorDep
) -> StreamingResponse:
    """Export a single contract's audit (with annotations + cross-reference) as PDF.

    Args:
        document_id: The audited document.
        tenant_id: The caller's tenant.
        actor: Who performed the export.

    Returns:
        A streaming PDF download.

    Raises:
        HTTPException: 404 if no persisted audit exists for the document.
    """
    built = await build_single_report(tenant_id, document_id)
    if built is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No audit found for document {document_id}.",
        )
    pdf, vendor = built
    filename = f"audit_{slugify(vendor)}_{date.today().isoformat()}.pdf"
    await insert_activity(
        tenant_id=tenant_id,
        actor=actor,
        action=ActivityAction.DOCUMENT_EXPORTED,
        document_id=document_id,
        metadata={"filename": filename},
    )
    return _pdf_response(pdf, filename)


@router.get("/portfolio/export/pdf")
async def export_portfolio_pdf(
    tenant_id: TenantIdDep, actor: ActorDep
) -> StreamingResponse:
    """Export the full tenant portfolio as a PDF report.

    Args:
        tenant_id: The caller's tenant.
        actor: Who performed the export.

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
    await insert_activity(
        tenant_id=tenant_id,
        actor=actor,
        action=ActivityAction.PORTFOLIO_EXPORTED,
        metadata={"contract_count": len(data["items"])},
    )
    return _pdf_response(pdf, filename)
