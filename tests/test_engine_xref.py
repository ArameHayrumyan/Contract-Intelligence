"""Tests for the cross-reference engine and the tenant-isolation guard."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from conftest import make_pdf
from rag_core.config import get_settings
from rag_core.engine_xref import CrossReferenceEngine
from rag_core.schemas_xref import (
    ClauseDeviation,
    ClauseInventory,
    ClauseInventoryItem,
    DeviationType,
)
from rag_core.storage import RetrievedChunk


class _FakeStructured:
    """The object returned by ``with_structured_output(schema)``."""

    def __init__(self, schema: type, llm: _FakeXrefLLM) -> None:
        self._schema = schema
        self._llm = llm

    def invoke(self, prompt: str) -> Any:
        return self._llm.structured_response(self._schema, prompt)


class _FakeXrefLLM:
    """Routes structured calls to canned inventories / deviation by prompt."""

    def __init__(
        self,
        *,
        subject_items: list[ClauseInventoryItem],
        standard_items: list[ClauseInventoryItem],
        deviation: ClauseDeviation | None,
    ) -> None:
        self._subject_items = subject_items
        self._standard_items = standard_items
        self._deviation = deviation

    def with_structured_output(self, schema: type) -> _FakeStructured:
        return _FakeStructured(schema, self)

    def invoke(self, prompt: str) -> Any:
        # Plain (non-structured) call — used for the executive summary.
        return SimpleNamespace(content="Executive summary.")

    def structured_response(self, schema: type, prompt: str) -> Any:
        if schema is ClauseInventory:
            if "subject contract" in prompt:
                return ClauseInventory(items=self._subject_items)
            return ClauseInventory(items=self._standard_items)
        if schema is ClauseDeviation:
            assert self._deviation is not None
            return self._deviation
        raise AssertionError(f"unexpected schema {schema!r}")


class _FakeXrefStore:
    """Configurable store for cross-reference unit tests (no Chroma/embeddings)."""

    def __init__(
        self,
        *,
        subject_chunks: list[RetrievedChunk],
        standard_chunks: list[RetrievedChunk],
        standards_match: list[RetrievedChunk],
        subject_match: list[RetrievedChunk],
    ) -> None:
        self._subject_chunks = subject_chunks
        self._standard_chunks = standard_chunks
        self._standards_match = standards_match
        self._subject_match = subject_match

    def get_document_chunks(
        self, tenant_id: str, document_id: str, *, kind: str = "contracts"
    ) -> list[RetrievedChunk]:
        return self._standard_chunks if kind == "standards" else self._subject_chunks

    def get_standard_version(self, tenant_id: str, standard_document_id: str) -> str:
        return "v1"

    def get_element_metadata(
        self, tenant_id: str, chunk_id: str, *, kind: str = "contracts"
    ) -> dict[str, Any]:
        # Plain-text elements for these tests (table comparison covered elsewhere).
        return {"element_type": "text", "column_headers": [], "structured_data": []}

    def query_standards(self, *args: Any, **kwargs: Any) -> list[RetrievedChunk]:
        return list(self._standards_match)

    def bm25_query_standards(self, *args: Any, **kwargs: Any) -> list[RetrievedChunk]:
        return list(self._standards_match)

    def query(self, *args: Any, **kwargs: Any) -> list[RetrievedChunk]:
        return list(self._subject_match)

    def bm25_query(self, *args: Any, **kwargs: Any) -> list[RetrievedChunk]:
        return list(self._subject_match)


def _chunk(chunk_id: str, text: str, page: int = 1) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id="doc",
        page_number=page,
        text=text,
        distance=0.0,
    )


async def test_weakened_deviation_detected() -> None:
    """A subject clause that reduces a liability cap is classified WEAKENED."""
    subject_chunk = _chunk("s1", "Limitation of Liability: capped at 2x annual fees.")
    standard_chunk = _chunk("t1", "Limitation of Liability: capped at 1x annual fees.")

    llm = _FakeXrefLLM(
        subject_items=[
            ClauseInventoryItem(
                clause_type="Limitation of Liability",
                text=subject_chunk.text,
                chunk_id="s1",
                page_number=1,
            )
        ],
        standard_items=[
            ClauseInventoryItem(
                clause_type="Limitation of Liability",
                text=standard_chunk.text,
                chunk_id="t1",
                page_number=1,
            )
        ],
        deviation=ClauseDeviation(
            clause_type="Limitation of Liability",
            subject_text="x",
            subject_chunk_id="s1",
            deviation_type=DeviationType.WEAKENED,
            severity=7,
            explanation="Subject doubles the liability cap relative to the standard.",
        ),
    )
    store = _FakeXrefStore(
        subject_chunks=[subject_chunk],
        standard_chunks=[standard_chunk],
        standards_match=[standard_chunk],  # alignment finds the counterpart
        subject_match=[subject_chunk],  # standard clause is present in subject
    )
    engine = CrossReferenceEngine(settings=get_settings(), store=store, llm=llm)  # type: ignore[arg-type]

    result = await engine.run("subj", "std", "acme")

    weakened = [d for d in result.deviations if d.deviation_type == DeviationType.WEAKENED]
    assert len(weakened) == 1
    assert weakened[0].severity >= 5
    assert weakened[0].standard_text == standard_chunk.text  # provenance reconciled


async def test_missing_clause_detected() -> None:
    """A standard clause with no counterpart in the subject is flagged MISSING."""
    subject_chunk = _chunk("s1", "Governing law is New York.")
    standard_chunk = _chunk("t1", "Data Breach Notification within 72 hours.")

    llm = _FakeXrefLLM(
        subject_items=[
            ClauseInventoryItem(
                clause_type="Governing Law", text=subject_chunk.text, chunk_id="s1"
            )
        ],
        standard_items=[
            ClauseInventoryItem(
                clause_type="Data Breach Notification",
                text=standard_chunk.text,
                chunk_id="t1",
            )
        ],
        deviation=None,  # no classify call expected (no alignment match)
    )
    store = _FakeXrefStore(
        subject_chunks=[subject_chunk],
        standard_chunks=[standard_chunk],
        standards_match=[],  # subject clause has no standard counterpart
        subject_match=[],  # standard clause is absent from the subject
    )
    engine = CrossReferenceEngine(settings=get_settings(), store=store, llm=llm)  # type: ignore[arg-type]

    result = await engine.run("subj", "std", "acme")

    missing = [d for d in result.deviations if d.deviation_type == DeviationType.MISSING]
    assert any(d.clause_type == "Data Breach Notification" for d in missing)


async def test_cross_reference_tenant_isolation_403(
    app, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    """A tenant cannot cross-reference against another tenant's standard (403)."""
    import dependencies

    # Add a second tenant's API key (real auth dependency, not mocked).
    monkeypatch.setitem(
        dependencies._API_KEYS, "key-tenant-b", ("user-b@b.example", "tenantb")
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        # tenant "acme" (default demo key) uploads a standard.
        std_resp = await ac.post(
            "/standards",
            headers={"X-API-Key": "demo-key-tenant-acme"},
            data={"standard_name": "Policy", "standard_version": "1.0"},
            files={"file": ("std.pdf", make_pdf(), "application/pdf")},
        )
        assert std_resp.status_code == 202
        standard_id = std_resp.json()["standard_document_id"]

        # tenant "tenantb" uploads a subject contract.
        doc_resp = await ac.post(
            "/documents",
            headers={"X-API-Key": "key-tenant-b"},
            files={"file": ("c.pdf", make_pdf(), "application/pdf")},
        )
        assert doc_resp.status_code == 202
        document_id = doc_resp.json()["document_id"]

        # tenantb references acme's standard id -> forbidden.
        resp = await ac.post(
            f"/documents/{document_id}/cross-reference",
            headers={"X-API-Key": "key-tenant-b"},
            json={"standard_document_id": standard_id},
        )
    assert resp.status_code == 403
