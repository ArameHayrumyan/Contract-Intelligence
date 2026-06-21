"""Shared single-document PDF assembly (used by exports + bulk export).

Keeps the "fetch persisted audit + cross-reference + annotations, then render"
logic in one place so the export router and the dashboard bulk-export endpoint
cannot drift apart.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from rag_core.database import (
    get_audit_result,
    get_crossref_by_subject,
    list_annotations,
)
from rag_core.report_generator import ReportGenerator
from rag_core.schemas import ContractAuditSchema, CriticalClause
from rag_core.schemas_xref import CrossReferenceAuditSchema

_generator = ReportGenerator()


def slugify(value: str) -> str:
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


async def build_single_report(
    tenant_id: str, document_id: str
) -> tuple[bytes, str] | None:
    """Build a single-document PDF from persisted data.

    Args:
        tenant_id: The caller's tenant.
        document_id: The audited document.

    Returns:
        ``(pdf_bytes, vendor_name)``, or ``None`` if no audit exists.
    """
    row = await get_audit_result(document_id, tenant_id)
    if row is None:
        return None

    crossref: CrossReferenceAuditSchema | None = None
    if row.get("has_crossref"):
        cr = await get_crossref_by_subject(document_id, tenant_id)
        if cr is not None:
            crossref = CrossReferenceAuditSchema.model_validate(cr["deviations"])

    annotations = await list_annotations(tenant_id, document_id)
    pdf = _generator.generate_single_document_report(
        audit=_row_to_audit(row),
        document_id=document_id,
        crossref=crossref,
        annotations=annotations,
        tenant_id=tenant_id,
    )
    return pdf, row["vendor_name"]
