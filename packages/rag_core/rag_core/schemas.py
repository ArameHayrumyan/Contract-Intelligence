"""Domain schemas for contract auditing.

The defining requirement here is **provenance**: every critical clause carries
the chunk id and page number it was drawn from. A risk assessment that cannot
point back to its source text is not auditable, which is unacceptable in a tool
with "Auditor" in the name (Architectural Constraint #3).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class RiskBand(StrEnum):
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
        contract_end_date: Expiry / auto-renewal date if stated, else ``None``.
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
    contract_end_date: date | None = Field(
        default=None,
        description=(
            "Contract expiry or auto-renewal date as written in the document. "
            "Return null if not explicitly stated."
        ),
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


class DocumentStatus(StrEnum):
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


class ExtractionMethod(StrEnum):
    """Which extractor produced an element (tiered parsing strategy)."""

    PDFPLUMBER_NATIVE = "pdfplumber_native"
    PYMUPDF_MULTICOLUMN = "pymupdf_multicolumn"
    CAMELOT_LATTICE = "camelot_lattice"
    CAMELOT_STREAM = "camelot_stream"
    OCR_PYTESSERACT = "ocr_pytesseract"
    OCR_IMG2TABLE = "ocr_img2table"


class DocumentElementType(StrEnum):
    """Structural kind of a parsed document element."""

    TEXT = "text"
    TABLE = "table"
    SECTION_HEADER = "section_header"


class TableElement(BaseModel):
    """A parsed table with both human-readable and structured representations.

    Attributes:
        element_type: Always ``TABLE``.
        page_number: 1-based source page.
        chunk_id: Stable id (``{document_id}_table_p{page}_{index}``).
        extraction_method: Which extractor produced it.
        markdown_representation: Pipe-delimited markdown, used for RAG chunking.
        column_headers: Header labels; empty when no header row was detected.
        structured_data: Raw cells ``[row][col]`` with the header row excluded;
            used for direct cell-level comparison in cross-referencing.
        tenant_id: Owning tenant (Constraint #2).
    """

    element_type: Literal[DocumentElementType.TABLE] = DocumentElementType.TABLE
    page_number: int
    chunk_id: str
    extraction_method: ExtractionMethod
    markdown_representation: str
    column_headers: list[str] = Field(default_factory=list)
    structured_data: list[list[str]] = Field(default_factory=list)
    tenant_id: str


class TextElement(BaseModel):
    """A parsed run of prose or a section header.

    Attributes:
        element_type: ``TEXT`` or ``SECTION_HEADER``.
        page_number: 1-based source page.
        chunk_id: Stable chunk id.
        extraction_method: Which extractor produced it.
        text: The extracted text.
        tenant_id: Owning tenant (Constraint #2).
    """

    element_type: Literal[
        DocumentElementType.TEXT, DocumentElementType.SECTION_HEADER
    ] = DocumentElementType.TEXT
    page_number: int
    chunk_id: str
    extraction_method: ExtractionMethod
    text: str
    tenant_id: str


class ParsedDocument(BaseModel):
    """The full tiered-parser output for one document (processor-internal).

    Attributes:
        document_id: Owning document.
        tenant_id: Owning tenant (Constraint #2).
        total_pages: Page count of the source PDF.
        elements: Ordered text/table elements across all pages.
        extraction_summary: Count of elements produced per extraction method.
    """

    document_id: str
    tenant_id: str
    total_pages: int
    elements: list[TableElement | TextElement] = Field(default_factory=list)
    extraction_summary: dict[ExtractionMethod, int] = Field(default_factory=dict)


class Chunk(BaseModel):
    """A persisted chunk carrying provenance and element metadata.

    Text chunks leave the table fields empty; table chunks carry the parsed
    grid so cross-referencing can compare cells directly. Defaults keep the
    common text-chunk construction unchanged.

    Attributes:
        chunk_id: Stable unique id for the chunk.
        document_id: Owning document.
        tenant_id: Owning tenant (Constraint #2).
        page_number: 1-based source page number.
        text: Chunk text content (table chunks store their markdown here).
        element_type: ``text`` / ``table`` / ``section_header``.
        extraction_method: Which extractor produced the source element.
        column_headers: Table header labels (empty for text chunks).
        structured_data: Table cells ``[row][col]`` (empty for text chunks).
    """

    chunk_id: str
    document_id: str
    tenant_id: str
    page_number: int | None
    text: str
    element_type: DocumentElementType = DocumentElementType.TEXT
    extraction_method: ExtractionMethod = ExtractionMethod.PDFPLUMBER_NATIVE
    column_headers: list[str] = Field(default_factory=list)
    structured_data: list[list[str]] = Field(default_factory=list)


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


# --- Human annotations & compliance activity log (Tier 2) -------------------


class AnnotationType(StrEnum):
    """Reviewer's classification of a human annotation."""

    ACCEPTED_RISK = "accepted_risk"
    ESCALATE_TO_LEGAL = "escalate_to_legal"
    DISPUTED = "disputed"
    REQUIRES_NEGOTIATION = "requires_negotiation"
    FALSE_POSITIVE = "false_positive"
    CUSTOM = "custom"


class ActivityAction(StrEnum):
    """Action recorded in the immutable compliance activity log."""

    AUDIT_RUN = "audit_run"
    CROSSREF_RUN = "crossref_run"
    STATUS_CHANGED = "status_changed"
    ANNOTATION_ADDED = "annotation_added"
    ANNOTATION_UPDATED = "annotation_updated"
    ANNOTATION_DELETED = "annotation_deleted"
    DOCUMENT_EXPORTED = "document_exported"
    PORTFOLIO_EXPORTED = "portfolio_exported"
    BULK_STATUS_CHANGED = "bulk_status_changed"
    BULK_EXPORTED = "bulk_exported"


class CreateAnnotationRequest(BaseModel):
    """Request body to create a human annotation on a target.

    Attributes:
        target_type: What the note is attached to.
        target_reference: ``chunk_id`` (clause) / ``deviation_id`` (deviation);
            ``None`` for document-level notes.
        annotation_type: Reviewer classification.
        note: The note text (10-2000 chars).
    """

    target_type: Literal["document", "clause", "deviation"]
    target_reference: str | None = None
    annotation_type: AnnotationType
    note: str = Field(..., min_length=10, max_length=2000)

    @model_validator(mode="after")
    def validate_reference_required(self) -> CreateAnnotationRequest:
        """Require a target reference for clause/deviation annotations."""
        if self.target_type in ("clause", "deviation") and not self.target_reference:
            raise ValueError(
                f"target_reference is required when "
                f"target_type is '{self.target_type}'"
            )
        return self


class UpdateAnnotationRequest(BaseModel):
    """Request body to edit an annotation's type and note."""

    annotation_type: AnnotationType
    note: str = Field(..., min_length=10, max_length=2000)


class AnnotationResponse(BaseModel):
    """A human annotation as returned by the API.

    Attributes:
        id: Annotation id.
        document_id: Owning document.
        target_type: What the note is attached to.
        target_reference: Clause/deviation reference, or ``None``.
        annotation_type: Reviewer classification.
        note: The note text.
        actor: Who recorded the note.
        created_at: Creation timestamp.
        updated_at: Last-edit timestamp.
    """

    id: str
    document_id: str
    target_type: str
    target_reference: str | None
    annotation_type: AnnotationType
    note: str
    actor: str
    created_at: datetime
    updated_at: datetime


class BulkStatusRequest(BaseModel):
    """Request body to change the workflow status of many contracts at once."""

    document_ids: list[str] = Field(..., min_length=1, max_length=50)
    status: str
    note: str | None = Field(None, max_length=500)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        """Validate the status against the allowed workflow set."""
        allowed = {"processing", "audited", "reviewed", "approved", "flagged"}
        if value not in allowed:
            raise ValueError(f"status must be one of {sorted(allowed)}")
        return value


class BulkExportRequest(BaseModel):
    """Request body to export many contracts as a single zip.

    Capped at 20: generating 20 PDFs synchronously is realistic on the current
    Droplet; beyond that a background task + download link is needed
    (see ``docs/SCALING_PATH.md``).
    """

    document_ids: list[str] = Field(..., min_length=1, max_length=20)
