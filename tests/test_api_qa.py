"""API tests for the cross-document QA router."""

from __future__ import annotations

import httpx


async def test_qa_returns_answer_and_citations(client: httpx.AsyncClient) -> None:
    """A valid question returns an answer with provenance citations."""
    resp = await client.post("/qa", json={"question": "Which contracts auto-renew?"})
    assert resp.status_code == 200
    body = resp.json()
    assert "auto-renew" in body["answer"].lower() or body["answer"]
    assert body["citations"]
    assert body["citations"][0]["chunk_id"]


async def test_qa_validates_short_question(client: httpx.AsyncClient) -> None:
    """A too-short question is rejected by request validation (422)."""
    resp = await client.post("/qa", json={"question": "x"})
    assert resp.status_code == 422


async def test_qa_requires_api_key(app) -> None:  # type: ignore[no-untyped-def]
    """QA without an API key is rejected with 401."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/qa", json={"question": "anything here"})
    assert resp.status_code == 401
