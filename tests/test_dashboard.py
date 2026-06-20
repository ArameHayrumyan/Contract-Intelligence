"""Tests for the portfolio dashboard endpoints."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from rag_core.database import upsert_audit_result
from rag_core.schemas import ContractAuditSchema

_HEADERS = {"X-API-Key": "demo-key-tenant-acme"}


def _audit(vendor: str, risk: int, *, auto_renewal: bool = False) -> ContractAuditSchema:
    return ContractAuditSchema(
        vendor_name=vendor,
        contract_type="MSA",
        auto_renewal=auto_renewal,
        notice_period_days=30,
        liability_cap_description="Capped at 12 months of fees.",
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


async def test_summary_reflects_single_audit(http: httpx.AsyncClient) -> None:
    """A single high-risk audit is counted and banded correctly."""
    await _seed("doc-1", "Acme", 9)

    resp = await http.get("/dashboard/summary")
    assert resp.status_code == 200
    body: dict[str, Any] = resp.json()
    assert body["total_contracts"] == 1
    assert body["risk_distribution"]["high"] == 1
    assert body["risk_distribution"]["low"] == 0


async def test_contracts_filter_by_min_risk(http: httpx.AsyncClient) -> None:
    """risk_score_min filters out lower-risk contracts."""
    await _seed("doc-low", "Low Corp", 2)
    await _seed("doc-mid", "Mid Corp", 6)
    await _seed("doc-high", "High Corp", 9)

    resp = await http.get("/dashboard/contracts", params={"risk_score_min": 8})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["vendor_name"] == "High Corp"


async def test_status_patch_persists(http: httpx.AsyncClient) -> None:
    """Patching workflow status is reflected in subsequent reads."""
    await _seed("doc-1", "Acme", 5)

    patch = await http.patch(
        "/dashboard/contracts/doc-1/status", json={"status": "flagged"}
    )
    assert patch.status_code == 200
    assert patch.json()["status"] == "flagged"

    listing = await http.get("/dashboard/contracts")
    item = next(i for i in listing.json()["items"] if i["document_id"] == "doc-1")
    assert item["status"] == "flagged"
