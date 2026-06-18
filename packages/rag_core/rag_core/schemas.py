"""Domain schemas for contract auditing.

The defining requirement here is **provenance**: every critical clause carries
the chunk id and page number it was drawn from. A risk assessment that cannot
point back to its source text is not auditable, which is unacceptable in a tool
with "Auditor" in the name (Architectural Constraint #3).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class RiskBand(str, Enum):
    """Human-readable banding aligned with the frontend ``RiskBadge`` colours."""

    LOW = "low"  # 1-3  (green)
    MEDIUM = "medium"  # 4-7  (amber)
    HIGH = "high"  # 8-10 (red)

    @classmethod
    def from_score(cls, score: int) -> RiskBand:
        """Map a 1-10 risk score onto a band.

        Args:
            score: Risk score in the inclusive range 1-10.

        Returns:
            The corresponding :class:`RiskBand`.
        """
        if score <= 3:
            return cls.LOW
        if score <= 7:
            return cls.MEDIUM
        return cls.HIGH


class CriticalClause(BaseModel):
    """A single risk-bearing clause with traceable provenance.

    Attributes:
        text: The exact clause text extracted from the contract.
        source_chunk_id: Identifier of the chunk the text came from.
        page_number: 1-based page number, when known.
        category: Optional short tag, e.g. ``"liability"`` or ``"termination"``.
    """

    text: str = Field(..., description="Exact clause text.")
    source_chunk_id: str = Field(..., description="Originating chunk id.")
    page_number: int | None = Field(
        default=None, ge=1, description="1-based source page number, if known."
    )
    category: str | None = Field(
        default=None, description="Short classification tag for the clause."
    )


class ContractAuditSchema(BaseModel):
    """Structured audit result for a single contract.

    This is the schema bound via ``.with_structured_output`` in the engine and
    returned verbatim by ``GET /documents/{id}/audit``.

    Attributes:
        vendor_name: Counterparty / vendor name.
        contract_type: Classification, e.g. ``"MSA"``, ``"SLA"``, ``"NDA"``.
        auto_renewal: Whether the contract auto-renews.
        notice_period_days: Termination notice period in days (>= 0).
        liability_cap_description: Free-text description of the liability cap.
        risk_score: Overall risk on a 1 (low) to 10 (high) scale.
        risk_rationale: Justification for ``risk_score``.
        critical_clauses: Risk-bearing clauses, each with provenance.
    """

    vendor_name: str = Field(..., description="Counterparty / vendor name.")
    contract_type: str = Field(..., description="Contract classification.")
    auto_renewal: bool = Field(..., description="Whether the contract auto-renews.")
    notice_period_days: int = Field(
        ..., ge=0, description="Termination notice period in days."
    )
    liability_cap_description: str = Field(
        ..., description="Description of the liability cap, or 'None found'."
    )
    risk_score: int = Field(..., ge=1, le=10, description="Overall risk, 1-10.")
    risk_rationale: str = Field(..., description="Why this risk score was assigned.")
    critical_clauses: list[CriticalClause] = Field(
        default_factory=list,
        description="Risk-bearing clauses with chunk-id + page provenance.",
    )

    @property
    def risk_band(self) -> RiskBand:
        """Convenience banding derived from :attr:`risk_score`."""
        return RiskBand.from_score(self.risk_score)


class DocumentStatus(str, Enum):
    """Lifecycle states for an ingested document."""

    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class DocumentRecord(BaseModel):
    """Tenant-scoped record describing an uploaded document and its progress.

    Attributes:
        document_id: Server-assigned identifier.
        tenant_id: Owning tenant (never optional — Constraint #2).
        filename: Original upload filename.
        status: Current ingestion lifecycle state.
        page_count: Number of pages detected during validation.
        chunk_count: Number of chunks persisted (populated when ready).
        error: Failure detail, present only when ``status`` is ``FAILED``.
        audit: Cached audit result, present only when computed.
    """

    document_id: str
    tenant_id: str
    filename: str
    status: DocumentStatus = DocumentStatus.PENDING
    page_count: int | None = None
    chunk_count: int | None = None
    error: str | None = None
    audit: ContractAuditSchema | None = None


class Chunk(BaseModel):
    """A persisted text chunk carrying provenance metadata.

    Attributes:
        chunk_id: Stable unique id for the chunk.
        document_id: Owning document.
        tenant_id: Owning tenant (Constraint #2).
        page_number: 1-based source page number.
        text: Chunk text content.
    """

    chunk_id: str
    document_id: str
    tenant_id: str
    page_number: int | None
    text: str


class QARequest(BaseModel):
    """Cross-document question-answering request, scoped to the caller's tenant.

    Attributes:
        question: Natural-language question over the tenant's document set.
        document_ids: Optional subset of documents to restrict the search to.
    """

    question: str = Field(..., min_length=3)
    document_ids: list[str] | None = None


class QACitation(BaseModel):
    """A source citation backing a QA answer.

    Attributes:
        chunk_id: Source chunk id.
        document_id: Source document id.
        page_number: 1-based source page, when known.
        snippet: Short excerpt of the supporting text.
    """

    chunk_id: str
    document_id: str
    page_number: int | None
    snippet: str


class QAResponse(BaseModel):
    """Answer to a :class:`QARequest` with supporting citations.

    Attributes:
        answer: Synthesised natural-language answer.
        citations: Provenance for the answer.
    """

    answer: str
    citations: list[QACitation] = Field(default_factory=list)
