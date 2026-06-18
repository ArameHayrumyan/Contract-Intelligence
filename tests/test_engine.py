"""Tests for the RAG engine: RRF fusion and provenance reconciliation."""

from __future__ import annotations

from rag_core.engine import (
    RRF_K,
    AuditEngine,
    reciprocal_rank_fusion,
)
from rag_core.schemas import ContractAuditSchema, CriticalClause
from rag_core.storage import RetrievedChunk


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
