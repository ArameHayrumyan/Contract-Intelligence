"""Tests for dashboard bulk operations (status + export)."""

from __future__ import annotations

import io
import zipfile

import httpx
import pytest

from rag_core.database import upsert_audit_result
from rag_core.schemas import ContractAuditSchema

_HEADERS = {"X-API-Key": "demo-key-tenant-acme"}  # -> tenant "acme"


def _audit(vendor: str) -> ContractAuditSchema:
    return ContractAuditSchema(
        vendor_name=vendor,
        contract_type="MSA",
        auto_renewal=False,
        notice_period_days=30,
        liability_cap_description="Capped.",
        risk_score=5,
        risk_rationale="Rationale.",
        critical_clauses=[],
    )


async def _seed(document_id: str, vendor: str, tenant_id: str = "acme") -> None:
    await upsert_audit_result(
        result=_audit(vendor),
        document_id=document_id,
        tenant_id=tenant_id,
        contract_end_date=None,
        actor="seed",
    )


@pytest.fixture()
async def http(app, audit_db):  # type: ignore[no-untyped-def]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=_HEADERS
    ) as ac:
        yield ac


async def test_bulk_status_update(http: httpx.AsyncClient) -> None:
    """Bulk status update flags all three and logs one bulk entry."""
    for i in range(3):
        await _seed(f"doc-{i}", f"Vendor {i}")

    resp = await http.post(
        "/dashboard/contracts/bulk/status",
        json={"document_ids": ["doc-0", "doc-1", "doc-2"], "status": "flagged"},
    )
    assert resp.status_code == 200
    assert resp.json()["updated"] == 3

    flagged = await http.get("/dashboard/contracts?status=flagged")
    assert flagged.json()["total"] == 3

    activity = await http.get("/activity")
    entry = next(
        a for a in activity.json()["items"] if a["action"] == "bulk_status_changed"
    )
    assert entry["metadata"]["count"] == 3


async def test_bulk_status_rejects_cross_tenant(http: httpx.AsyncClient) -> None:
    """A foreign id rejects the whole batch (403) and updates nothing."""
    await _seed("doc-a1", "A One")
    await _seed("doc-a2", "A Two")
    await _seed("doc-b", "B One", tenant_id="tenant-b")

    resp = await http.post(
        "/dashboard/contracts/bulk/status",
        json={"document_ids": ["doc-a1", "doc-a2", "doc-b"], "status": "flagged"},
    )
    assert resp.status_code == 403

    flagged = await http.get("/dashboard/contracts?status=flagged")
    assert flagged.json()["total"] == 0  # all-or-nothing: nothing updated


async def test_bulk_export_returns_zip(http: httpx.AsyncClient) -> None:
    """Bulk export returns a zip containing one PDF per document."""
    await _seed("doc-0", "Acme")
    await _seed("doc-1", "Globex")

    resp = await http.post(
        "/dashboard/contracts/bulk/export",
        json={"document_ids": ["doc-0", "doc-1"]},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
        names = archive.namelist()
    assert "ERRORS.txt" not in names
    assert len([n for n in names if n.endswith(".pdf")]) == 2
