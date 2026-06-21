"""Tests for human annotations (document / clause / deviation level)."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from rag_core import database
from rag_core.database import upsert_audit_result
from rag_core.schemas import ContractAuditSchema

_HEADERS = {"X-API-Key": "demo-key-tenant-acme"}  # -> tenant "acme"


def _audit(vendor: str = "Acme") -> ContractAuditSchema:
    return ContractAuditSchema(
        vendor_name=vendor,
        contract_type="MSA",
        auto_renewal=False,
        notice_period_days=30,
        liability_cap_description="Capped.",
        risk_score=7,
        risk_rationale="Rationale.",
        critical_clauses=[],
    )


async def _seed(document_id: str, tenant_id: str = "acme") -> None:
    await upsert_audit_result(
        result=_audit(),
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


async def test_create_and_retrieve_clause_annotation(
    app, http: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clause annotation is created and returned with correct fields."""
    await _seed("doc-1")
    monkeypatch.setattr(
        app.state.service, "chunk_exists", lambda **_: True
    )

    resp = await http.post(
        "/documents/doc-1/annotations",
        json={
            "target_type": "clause",
            "target_reference": "chunk-xyz",
            "annotation_type": "accepted_risk",
            "note": "Risk reviewed and accepted by legal counsel.",
        },
    )
    assert resp.status_code == 201

    listing = await http.get("/documents/doc-1/annotations?target_type=clause")
    items = listing.json()
    assert len(items) == 1
    assert items[0]["target_reference"] == "chunk-xyz"
    assert items[0]["annotation_type"] == "accepted_risk"


async def test_deviation_annotation_requires_reference(
    http: httpx.AsyncClient,
) -> None:
    """A deviation annotation with no target_reference is rejected (422)."""
    await _seed("doc-1")
    resp = await http.post(
        "/documents/doc-1/annotations",
        json={
            "target_type": "deviation",
            "annotation_type": "disputed",
            "note": "This deviation is disputed by the vendor.",
        },
    )
    assert resp.status_code == 422
    assert "target_reference" in resp.text


async def test_cross_tenant_annotation_blocked(http: httpx.AsyncClient) -> None:
    """Annotating another tenant's document returns 403."""
    await _seed("doc-other", tenant_id="tenant-a")  # not acme's
    resp = await http.post(
        "/documents/doc-other/annotations",
        json={
            "target_type": "document",
            "annotation_type": "custom",
            "note": "Trying to annotate a foreign document.",
        },
    )
    assert resp.status_code == 403


async def test_soft_delete_is_not_hard_delete(http: httpx.AsyncClient) -> None:
    """A deleted annotation is hidden but its row physically remains."""
    await _seed("doc-1")
    created = await http.post(
        "/documents/doc-1/annotations",
        json={
            "target_type": "document",
            "annotation_type": "custom",
            "note": "A note that will be soft-deleted shortly.",
        },
    )
    annotation_id = created.json()["id"]

    deleted = await http.delete(f"/documents/doc-1/annotations/{annotation_id}")
    assert deleted.status_code == 204

    listing = await http.get("/documents/doc-1/annotations")
    assert all(a["id"] != annotation_id for a in listing.json())

    # The row must still exist with deleted_at populated (regression guard).
    async with database._require_engine().connect() as conn:
        row = (
            await conn.execute(
                select(database.annotations_table).where(
                    database.annotations_table.c.id == annotation_id
                )
            )
        ).mappings().first()
    assert row is not None
    assert row["deleted_at"] is not None


async def test_activity_logged_on_annotation(http: httpx.AsyncClient) -> None:
    """Creating an annotation writes an annotation_added activity entry."""
    await _seed("doc-1")
    await http.post(
        "/documents/doc-1/annotations",
        json={
            "target_type": "document",
            "annotation_type": "custom",
            "note": "A note to verify activity logging works.",
        },
    )
    activity = await http.get("/activity")
    items = activity.json()["items"]
    assert items[0]["action"] == "annotation_added"
    assert items[0]["document_id"] == "doc-1"
