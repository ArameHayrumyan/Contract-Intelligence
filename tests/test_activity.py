"""Tests for the immutable compliance activity log."""

from __future__ import annotations

import inspect

import httpx
import pytest

from conftest import make_pdf
from rag_core import database

_HEADERS = {"X-API-Key": "demo-key-tenant-acme"}


@pytest.fixture()
async def http(app, audit_db):  # type: ignore[no-untyped-def]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=_HEADERS
    ) as ac:
        yield ac


def test_activity_log_is_append_only() -> None:
    """Static guard: no UPDATE/DELETE against activity_log exists in the module."""
    source = inspect.getsource(database)
    assert "DELETE FROM activity_log" not in source
    assert "UPDATE activity_log" not in source
    # SQLAlchemy-style guards too (we never build raw SQL strings).
    assert "activity_log.delete(" not in source
    assert "activity_log.update(" not in source


async def test_full_lifecycle_is_logged(http: httpx.AsyncClient) -> None:
    """audit → status → annotation → export are logged in chronological order."""
    upload = await http.post(
        "/documents", files={"file": ("c.pdf", make_pdf(), "application/pdf")}
    )
    document_id = upload.json()["document_id"]

    assert (await http.get(f"/documents/{document_id}/audit")).status_code == 200
    assert (
        await http.patch(
            f"/dashboard/contracts/{document_id}/status",
            json={"status": "reviewed"},
        )
    ).status_code == 200
    assert (
        await http.post(
            f"/documents/{document_id}/annotations",
            json={
                "target_type": "document",
                "annotation_type": "custom",
                "note": "A document-level review note for the lifecycle test.",
            },
        )
    ).status_code == 201
    assert (
        await http.get(f"/documents/{document_id}/export/pdf")
    ).status_code == 200

    activity = await http.get(f"/documents/{document_id}/activity")
    items = activity.json()["items"]
    chronological = [entry["action"] for entry in reversed(items)]
    assert chronological == [
        "audit_run",
        "status_changed",
        "annotation_added",
        "document_exported",
    ]
