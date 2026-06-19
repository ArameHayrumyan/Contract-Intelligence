"""Schemas for the cross-reference audit workflow.

This is a *parallel* workflow to the single-contract audit: it compares a subject
contract against a versioned corporate standard, clause by clause, classifying
how each clause deviates and assigning severity. These types are independent of
:mod:`rag_core.schemas` — ``ContractAuditSchema`` and ``CriticalClause`` are not
touched.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class DeviationType(StrEnum):
    """How a subject clause deviates from the corporate standard.

    Attributes:
        MISSING: Clause exists in the standard but is absent from the subject.
        WEAKENED: Same clause, but the subject reduces an obligation / liability
            cap relative to the standard.
        STRENGTHENED: Same clause, but the subject increases an obligation
            (worth flagging for negotiation, not necessarily a risk).
        CONTRADICTORY: Direct logical conflict between subject and standard text.
        UNADDRESSED: A subject clause type has no counterpart in the standard.
    """

    MISSING = "missing"
    WEAKENED = "weakened"
    STRENGTHENED = "strengthened"
    CONTRADICTORY = "contradictory"
    UNADDRESSED = "unaddressed"


class ClauseInventoryItem(BaseModel):
    """A normalized clause extracted from a document (phase 1 output).

    The ``clause_type`` is a normalized English label (e.g. "Limitation of
    Liability") independent of the document's original section numbering, which is
    what makes alignment across differently-structured documents possible.

    Attributes:
        clause_type: Normalized clause-type label.
        text: Exact extracted clause text.
        chunk_id: Provenance — the chunk the text came from.
        page_number: 1-based source page, when known.
    """

    clause_type: str = Field(..., description="Normalized clause-type label.")
    text: str = Field(..., description="Exact extracted clause text.")
    chunk_id: str = Field(..., description="Originating chunk id.")
    page_number: int | None = Field(default=None, ge=1)


class ClauseInventory(BaseModel):
    """Wrapper enabling structured extraction of a clause list.

    ``with_structured_output`` binds a single model, so the list of inventory
    items is wrapped here.

    Attributes:
        items: The extracted, normalized clauses.
    """

    items: list[ClauseInventoryItem] = Field(default_factory=list)


class ClauseDeviation(BaseModel):
    """A single clause-level deviation with provenance on both sides.

    Attributes:
        clause_type: Normalized clause-type label.
        subject_text: Exact text from the subject contract.
        subject_chunk_id: Provenance from the subject document.
        subject_page: 1-based subject page, when known.
        standard_text: Standard's text; ``None`` when ``deviation_type`` is
            ``MISSING`` / ``UNADDRESSED``.
        standard_chunk_id: Standard provenance; ``None`` when no counterpart.
        standard_page: 1-based standard page, when known.
        deviation_type: How the subject deviates from the standard.
        severity: Deviation severity, 1 (trivial) to 10 (critical).
        explanation: Multi-sentence rationale mapping the deviation to specific
            language differences.
    """

    clause_type: str
    subject_text: str
    subject_chunk_id: str
    subject_page: int | None = Field(default=None, ge=1)
    standard_text: str | None = None
    standard_chunk_id: str | None = None
    standard_page: int | None = Field(default=None, ge=1)
    deviation_type: DeviationType
    severity: int = Field(..., ge=1, le=10)
    explanation: str


class CrossReferenceAuditSchema(BaseModel):
    """Structured result of cross-referencing a contract against a standard.

    Attributes:
        subject_document_id: The audited contract.
        standard_document_id: The standard version compared against.
        standard_version: Version label of the standard.
        deviations: All detected clause-level deviations.
        overall_risk_score: 1-10, computed programmatically from deviation count
            and severity weighting (not the LLM's free opinion).
        executive_summary: 3-5 sentence summary for non-legal stakeholders.
        tenant_id: Owning tenant (Constraint #2 — never omitted).
    """

    subject_document_id: str
    standard_document_id: str
    standard_version: str
    deviations: list[ClauseDeviation] = Field(default_factory=list)
    overall_risk_score: int = Field(..., ge=1, le=10)
    executive_summary: str
    tenant_id: str


class StandardRecord(BaseModel):
    """A registered standard document version.

    Attributes:
        standard_document_id: Server-assigned id for this version.
        standard_name: Human-readable name (grouping key across versions).
        standard_version: Version label.
        tenant_id: Owning tenant.
        status: Ingestion lifecycle state (reuses document statuses).
        chunk_count: Number of chunks persisted, when ready.
        error: Failure detail, when failed.
    """

    standard_document_id: str
    standard_name: str
    standard_version: str
    tenant_id: str
    status: str = "pending"
    chunk_count: int | None = None
    error: str | None = None
