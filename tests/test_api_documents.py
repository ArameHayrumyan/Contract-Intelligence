"""API tests for the documents router: upload validation, status, auth, audit."""

from __future__ import annotations

import httpx
import pytest

from conftest import make_pdf


async def test_upload_requires_api_key(app) -> None:  # type: ignore[no-untyped-def]
    """A request without an API key is rejected with 401."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/documents", files={"file": ("c.pdf", make_pdf(), "application/pdf")}
        )
    assert resp.status_code == 401


async def test_upload_rejects_non_pdf(client: httpx.AsyncClient) -> None:
    """A non-PDF payload fails the magic-byte sniff (422, reason not_pdf)."""
    resp = await client.post(
        "/documents",
        files={"file": ("evil.pdf", b"MZ\x90\x00 not a pdf", "application/pdf")},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["reason"] == "not_pdf"


async def test_upload_rejects_oversized(
    client: httpx.AsyncClient, settings
) -> None:  # type: ignore[no-untyped-def]
    """An upload exceeding the configured size cap is rejected (too_large)."""
    settings.max_upload_bytes = 100  # shrink the cap for this test
    resp = await client.post(
        "/documents",
        files={"file": ("big.pdf", make_pdf(pages=3), "application/pdf")},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["reason"] == "too_large"


async def test_upload_rejects_too_many_pages(
    client: httpx.AsyncClient, settings
) -> None:  # type: ignore[no-untyped-def]
    """An upload over the page cap is rejected (too_many_pages)."""
    settings.max_pages = 1
    resp = await client.post(
        "/documents",
        files={"file": ("many.pdf", make_pdf(pages=3), "application/pdf")},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["reason"] == "too_many_pages"


async def test_upload_then_status_ready(client: httpx.AsyncClient) -> None:
    """A valid upload ingests synchronously and reaches 'ready'."""
    resp = await client.post(
        "/documents",
        files={"file": ("good.pdf", make_pdf(), "application/pdf")},
    )
    assert resp.status_code == 202
    document_id = resp.json()["document_id"]

    status_resp = await client.get(f"/documents/{document_id}")
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["status"] == "ready"
    assert body["chunk_count"] == 1


async def test_status_unknown_document_404(client: httpx.AsyncClient) -> None:
    """Polling an unknown document id returns 404."""
    resp = await client.get("/documents/does-not-exist")
    assert resp.status_code == 404


async def test_audit_after_ingestion(client: httpx.AsyncClient) -> None:
    """The audit endpoint returns a provenance-bearing schema once ready."""
    upload = await client.post(
        "/documents", files={"file": ("good.pdf", make_pdf(), "application/pdf")}
    )
    document_id = upload.json()["document_id"]

    resp = await client.get(f"/documents/{document_id}/audit")
    assert resp.status_code == 200
    audit = resp.json()
    assert 1 <= audit["risk_score"] <= 10
    assert audit["critical_clauses"][0]["source_chunk_id"]
    assert audit["critical_clauses"][0]["page_number"] is not None
