"""Tests for the SLA & renewal monitoring endpoints."""

from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest

from rag_core.database import upsert_audit_result
from rag_core.schemas import ContractAuditSchema

_HEADERS = {"X-API-Key": "demo-key-tenant-acme"}


def _audit(vendor: str, *, auto_renewal: bool) -> ContractAuditSchema:
    return ContractAuditSchema(
        vendor_name=vendor,
        contract_type="MSA",
        auto_renewal=auto_renewal,
        notice_period_days=30,
        liability_cap_description="Capped.",
        risk_score=5,
        risk_rationale="Rationale.",
        critical_clauses=[],
    )


async def _seed(
    document_id: str, vendor: str, *, auto_renewal: bool, end_date: date | None
) -> None:
    await upsert_audit_result(
        result=_audit(vendor, auto_renewal=auto_renewal),
        document_id=document_id,
        tenant_id="acme",
        contract_end_date=end_date,
    )


@pytest.fixture()
async def http(app, audit_db):  # type: ignore[no-untyped-def]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=_HEADERS
    ) as ac:
        yield ac


async def test_renewal_in_first_window(http: httpx.AsyncClient) -> None:
    """A contract expiring in 25 days lands in the first (30-day) window."""
    await _seed(
        "doc-1", "Soon Corp", auto_renewal=True, end_date=date.today() + timedelta(days=25)
    )

    resp = await http.get("/monitoring/renewals", params={"thresholds": "30,60,90"})
    assert resp.status_code == 200
    windows = resp.json()["windows"]
    assert windows[0]["threshold_days"] == 30
    assert windows[0]["count"] == 1
    assert windows[0]["contracts"][0]["document_id"] == "doc-1"


async def test_unknown_date_group(http: httpx.AsyncClient) -> None:
    """An auto-renewing contract with no end date appears in unknown_date."""
    await _seed("doc-x", "NoDate Corp", auto_renewal=True, end_date=None)

    resp = await http.get(
        "/monitoring/renewals", params={"include_no_date": "true"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["unknown_date"]["count"] == 1
    assert body["unknown_date"]["contracts"][0]["document_id"] == "doc-x"


async def test_thresholds_round_trip(http: httpx.AsyncClient) -> None:
    """Saved thresholds are returned by the GET endpoint."""
    patch = await http.patch("/monitoring/thresholds", json={"thresholds": [45, 90, 180]})
    assert patch.status_code == 200
    assert patch.json()["thresholds"] == [45, 90, 180]

    get = await http.get("/monitoring/thresholds")
    assert get.json()["thresholds"] == [45, 90, 180]


async def test_thresholds_must_be_ascending(http: httpx.AsyncClient) -> None:
    """Non-ascending thresholds are rejected with 422."""
    resp = await http.patch("/monitoring/thresholds", json={"thresholds": [90, 30, 180]})
    assert resp.status_code == 422
