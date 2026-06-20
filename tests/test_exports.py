"""Tests for the PDF export endpoints."""

from __future__ import annotations

import httpx
import pytest

from rag_core.database import upsert_audit_result
from rag_core.schemas import ContractAuditSchema

_HEADERS = {"X-API-Key": "demo-key-tenant-acme"}


def _audit(vendor: str, risk: int) -> ContractAuditSchema:
    return ContractAuditSchema(
        vendor_name=vendor,
        contract_type="MSA",
        auto_renewal=True,
        notice_period_days=30,
        liability_cap_description="Capped at fees.",
        risk_score=risk,
        risk_rationale="Rationale.",
        critical_clauses=[],
    )


async def _seed(document_id: str, vendor: str, risk: int) -> None:
    await upsert_audit_result(
        result=_audit(vendor, risk),
        document_id=document_id,
        tenant_id="acme",
        contract_end_date=None,
    )


@pytest.fixture()
async def http(app, audit_db):  # type: ignore[no-untyped-def]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=_HEADERS
    ) as ac:
        yield ac


async def test_single_document_export(http: httpx.AsyncClient) -> None:
    """A seeded audit exports as a non-trivial PDF."""
    await _seed("doc-1", "Acme Corp", 7)

    resp = await http.get("/documents/doc-1/export/pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert len(resp.content) > 1000  # a real (non-empty) PDF


async def test_export_missing_document_404(http: httpx.AsyncClient) -> None:
    """Exporting an unknown document returns 404."""
    resp = await http.get("/documents/nonexistent/export/pdf")
    assert resp.status_code == 404


async def test_portfolio_export(http: httpx.AsyncClient) -> None:
    """The portfolio export returns a PDF across multiple contracts."""
    await _seed("doc-1", "Acme", 3)
    await _seed("doc-2", "Globex", 6)
    await _seed("doc-3", "Initech", 9)

    resp = await http.get("/portfolio/export/pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert len(resp.content) > 1000
