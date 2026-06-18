"""Tests for the RAG engine: RRF fusion and provenance reconciliation."""

from __future__ import annotations

from typing import Any

from rag_core.config import get_settings
from rag_core.engine import (
    RRF_K,
    AuditEngine,
    reciprocal_rank_fusion,
)
from rag_core.schemas import Chunk, ContractAuditSchema, CriticalClause
from rag_core.storage import RetrievedChunk, TenantVectorStore


def _chunk(chunk_id: str, distance: float = 0.1) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id="doc-1",
        page_number=3,
        text=f"text-{chunk_id}",
        distance=distance,
    )


def test_rrf_rewards_consensus_across_lists() -> None:
    """A chunk ranked highly in multiple lists outranks a single-list leader."""
    list_a = [_chunk("a"), _chunk("b"), _chunk("c")]
    list_b = [_chunk("b"), _chunk("a"), _chunk("d")]
    list_c = [_chunk("b"), _chunk("e"), _chunk("a")]

    fused = reciprocal_rank_fusion([list_a, list_b, list_c])
    ids = [c.chunk_id for c in fused]

    # "b" appears top-ish in all three → should win; "a" second.
    assert ids[0] == "b"
    assert ids[1] == "a"
    # No duplicates after fusion.
    assert len(ids) == len(set(ids))


def test_rrf_uses_expected_constant() -> None:
    """The RRF damping constant matches the spec (k=60)."""
    assert RRF_K == 60


def test_rrf_keeps_closest_distance_representative() -> None:
    """When a chunk appears twice, the lower-distance copy is kept."""
    fused = reciprocal_rank_fusion(
        [[_chunk("a", distance=0.9)], [_chunk("a", distance=0.1)]]
    )
    assert len(fused) == 1
    assert fused[0].distance == 0.1


def test_repair_provenance_overwrites_page_from_retrieval() -> None:
    """Page numbers are set authoritatively from retrieval, not the model."""
    audit = ContractAuditSchema(
        vendor_name="V",
        contract_type="MSA",
        auto_renewal=False,
        notice_period_days=0,
        liability_cap_description="None",
        risk_score=5,
        risk_rationale="r",
        critical_clauses=[
            CriticalClause(text="t", source_chunk_id="a", page_number=999)
        ],
    )
    chunks = [_chunk("a")]  # page_number == 3

    repaired = AuditEngine._repair_provenance(audit, chunks)
    assert repaired.critical_clauses[0].page_number == 3


class _OutlierEmbedding:
    """Deterministic fake embedding that makes the target a vector outlier.

    Chunks containing the marker word ``penalty`` embed orthogonally to
    everything else. The query and all distractors lack that word, so pure vector
    search ranks the distractors above the target — the target falls outside the
    vector top-k. This isolates the regression to the lexical path: only BM25 (via
    the exact identifier) can rescue the target, so its presence in the fused
    result proves hybrid search is doing real work.

    All of ``__call__`` / ``embed_documents`` / ``embed_query`` are implemented so
    the fake satisfies both the legacy and current chromadb embedding-function
    protocols (newer chromadb routes queries through ``embed_query``).
    """

    @staticmethod
    def _vectors(texts: str | list[str]) -> list[list[float]]:
        items = [texts] if isinstance(texts, str) else list(texts)
        return [[0.0, 1.0] if "penalty" in t.lower() else [1.0, 0.0] for t in items]

    def __call__(self, input: str | list[str]) -> list[list[float]]:  # noqa: A002
        return self._vectors(input)

    def embed_documents(self, input: str | list[str]) -> list[list[float]]:  # noqa: A002
        return self._vectors(input)

    def embed_query(self, input: str | list[str]) -> list[list[float]]:  # noqa: A002
        return self._vectors(input)

    def name(self) -> str:  # Some chromadb versions require a named function.
        return "outlier-test-embedding"


def test_hybrid_search_retrieves_exact_identifier() -> None:
    """An exact identifier query retrieves the right chunk that vectors miss.

    Regression for hybrid (BM25 + vector) retrieval: a clause keyed by an exact
    reference (``Section 4.1.a``) must surface even when its vector-similarity
    rank alone would place it outside the top results.
    """
    import chromadb

    settings = get_settings()
    store = TenantVectorStore(
        settings,
        client=chromadb.EphemeralClient(),
        embedding_function=_OutlierEmbedding(),  # type: ignore[arg-type]
    )
    tenant = "acme"

    target = Chunk(
        chunk_id="target",
        document_id="doc-1",
        tenant_id=tenant,
        page_number=4,
        text="Section 4.1.a Late payment penalty: the vendor shall pay 10%.",
    )
    # Enough distractors that the target cannot sneak into a top-10 vector result.
    distractors = [
        Chunk(
            chunk_id=f"d{i}",
            document_id="doc-1",
            tenant_id=tenant,
            page_number=1,
            text=f"General provision number {i} about services, scope and delivery.",
        )
        for i in range(12)
    ]
    store.add_chunks(tenant, [*distractors, target])

    query = "Section 4.1.a"

    # Pure vector search misses the target (it is a deliberate vector outlier).
    vector_hits = {c.chunk_id for c in store.query(tenant, query, top_k=10)}
    assert "target" not in vector_hits

    # BM25 finds it via the exact identifier token.
    bm25_hits = {c.chunk_id for c in store.bm25_query(tenant, query, top_k=10)}
    assert "target" in bm25_hits

    # Hybrid fusion surfaces it in the final result set.
    engine = AuditEngine(settings=settings, store=store, llm=_unused_llm())
    fused = engine._fused_retrieval(
        tenant,
        (query, f"clause regarding {query}", f"terms of {query}"),
    )
    assert "target" in {c.chunk_id for c in fused}


def _unused_llm() -> Any:
    """Return a sentinel LLM; retrieval tests never invoke generation."""
    return object()
