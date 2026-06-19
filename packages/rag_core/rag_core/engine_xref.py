"""Cross-reference engine: clause-level contract-vs-standard comparison.

This is the architecture that makes the feature *auditable* rather than a vague
"are these different?" summary. Four explicit phases:

    1. Inventory   - the LLM extracts a normalized clause inventory from each
                     document (clause type -> text), independent of the original
                     numbering, so clauses can be aligned across differently
                     structured documents.
    2. Alignment   - for each subject clause, hybrid search (BM25 + vector, the
                     existing RRF, k=60) finds the standard's counterpart even
                     under a different heading; for each standard clause, the
                     subject is searched to detect clauses missing from it.
    3. Classify    - each aligned pair is classified into a :class:`DeviationType`
                     with a severity, via structured generation (concurrency
                     capped to protect free-tier rate limits).
    4. Score       - the overall risk is computed *programmatically* from the
                     deviations (count x severity x type-weight), then a short
                     executive summary is generated.

It reuses ``rag_core.engine`` primitives (RRF, the retry policy, the retryable
classifier) rather than duplicating them.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TypeVar, cast

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel

from rag_core.config import Settings
from rag_core.engine import (
    RRF_K,
    RateLimitError,
    _is_retryable,
    build_retrying,
    reciprocal_rank_fusion_scored,
)
from rag_core.schemas_xref import (
    ClauseDeviation,
    ClauseInventory,
    ClauseInventoryItem,
    CrossReferenceAuditSchema,
    DeviationType,
)
from rag_core.storage import RetrievedChunk, TenantVectorStore

logger = logging.getLogger("rag_core.engine_xref")

#: Bound to Pydantic models for the generic structured-output helpers.
_StructuredT = TypeVar("_StructuredT", bound=BaseModel)

#: Top-k pulled from each retriever (vector and BM25) per clause alignment.
XREF_PER_RETRIEVER_TOP_K = 8

#: Minimum fused RRF score for a clause to be considered "found" in the other
#: document. A genuine counterpart appears near the top of *both* retrievers
#: (~2/RRF_K); a non-match appears only in the always-returning vector list at a
#: single low rank (~1/RRF_K). 1.5/RRF_K sits between the two.
XREF_MIN_RRF_SCORE = 1.5 / RRF_K

#: Cap on concurrent classification calls — protects free-tier rate limits while
#: still removing serial latency. Kept low (2) so a burst stays well under a
#: free-tier per-minute token window; raise on a paid tier for lower latency.
XREF_MAX_CONCURRENCY = 2

#: Char budget for the per-document inventory prompt. Sending an entire document
#: can be ~10k tokens in one call and blow a free-tier TPM window on its own;
#: this caps a single inventory call to roughly ~3k tokens. Small contracts fit
#: whole; very large ones are truncated (documented tradeoff).
XREF_INVENTORY_MAX_CHARS = 12000

#: Default severities for deviations decided structurally (no LLM call).
_MISSING_DEFAULT_SEVERITY = 6  # standard requires a clause the contract omits
_UNADDRESSED_DEFAULT_SEVERITY = 3  # contract has a clause the standard ignores

#: Type weights for the programmatic risk score, by legal-risk reasoning:
#: a missing-required clause or a direct contradiction is the most dangerous;
#: a weakened protection is serious; an unaddressed extra clause is mostly
#: informational; a strengthened obligation is usually favourable (negotiation
#: signal, not a risk).
DEVIATION_WEIGHTS: dict[DeviationType, float] = {
    DeviationType.MISSING: 1.0,
    DeviationType.CONTRADICTORY: 1.0,
    DeviationType.WEAKENED: 0.8,
    DeviationType.UNADDRESSED: 0.4,
    DeviationType.STRENGTHENED: 0.2,
}


def _as_number(value: str) -> float | None:
    """Parse a table cell as a number (ignoring %, $, commas), else ``None``.

    Args:
        value: A table cell.

    Returns:
        The numeric value, or ``None`` if it is not numeric.
    """
    cleaned = value.strip().rstrip("%").lstrip("$").replace(",", "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


class CrossReferenceEngine:
    """Compares a subject contract against a corporate standard, clause by clause."""

    def __init__(
        self,
        *,
        settings: Settings,
        store: TenantVectorStore,
        llm: BaseChatModel,
    ) -> None:
        """Initialise the engine.

        Args:
            settings: Application settings (retry budget, etc.).
            store: Tenant-scoped vector store (contracts + standards).
            llm: The chat model resolved by ``LLMProviderFactory``.
        """
        self._settings = settings
        self._store = store
        self._llm = llm

    # --- Public API ----------------------------------------------------------

    async def run(
        self,
        subject_document_id: str,
        standard_document_id: str,
        tenant_id: str,
    ) -> CrossReferenceAuditSchema:
        """Cross-reference a subject contract against a standard.

        Args:
            subject_document_id: The contract to audit.
            standard_document_id: The standard version to compare against.
            tenant_id: The owning tenant (both documents must belong to it).

        Returns:
            A populated :class:`CrossReferenceAuditSchema`.

        Raises:
            ValueError: If either document has no indexed content.
        """
        subject_chunks = self._store.get_document_chunks(
            tenant_id, subject_document_id
        )
        standard_chunks = self._store.get_document_chunks(
            tenant_id, standard_document_id, kind="standards"
        )
        if not subject_chunks:
            raise ValueError(f"Subject {subject_document_id} has no indexed content.")
        if not standard_chunks:
            raise ValueError(f"Standard {standard_document_id} has no indexed content.")

        standard_version = (
            self._store.get_standard_version(tenant_id, standard_document_id)
            or standard_document_id
        )

        # Phase 1 - inventories (concurrent: two independent extractions).
        subject_inv, standard_inv = await asyncio.gather(
            self._extract_inventory(subject_chunks, "subject contract"),
            self._extract_inventory(standard_chunks, "corporate standard"),
        )

        # Phases 2+3 - per subject clause: align to the standard, then classify.
        deviations = await self._classify_subject_clauses(
            tenant_id=tenant_id,
            standard_document_id=standard_document_id,
            subject_inventory=subject_inv,
        )

        # Phase 2b - standard clauses absent from the subject => MISSING.
        deviations.extend(
            self._detect_missing(
                tenant_id=tenant_id,
                subject_document_id=subject_document_id,
                standard_inventory=standard_inv,
            )
        )

        # Phase 4 - programmatic score + executive summary.
        overall = self._compute_risk_score(deviations)
        summary = await self._executive_summary(deviations, overall)

        deviations.sort(key=lambda d: d.severity, reverse=True)
        logger.info(
            "Cross-reference complete tenant=%s subject=%s standard=%s "
            "deviations=%d risk=%d",
            tenant_id,
            subject_document_id,
            standard_document_id,
            len(deviations),
            overall,
        )
        return CrossReferenceAuditSchema(
            subject_document_id=subject_document_id,
            standard_document_id=standard_document_id,
            standard_version=standard_version,
            deviations=deviations,
            overall_risk_score=overall,
            executive_summary=summary,
            tenant_id=tenant_id,
        )

    # --- Phase 1: inventory --------------------------------------------------

    async def _extract_inventory(
        self, chunks: list[RetrievedChunk], document_role: str
    ) -> list[ClauseInventoryItem]:
        """Extract a normalized clause inventory from a document's chunks.

        Args:
            chunks: All chunks of the document.
            document_role: Human label for the prompt ("subject contract" etc.).

        Returns:
            Normalized clause items with provenance reconciled to real chunks.
        """
        context = self._format_chunks(self._cap_for_inventory(chunks))
        prompt = (
            f"You are a contracts analyst. From the {document_role} excerpts "
            "below, extract a clause inventory: one entry per MATERIAL clause "
            "you can identify.\n\n"
            "Rules:\n"
            "- 'clause_type' is a NORMALIZED English label (e.g. 'Limitation of "
            "Liability', 'Termination for Convenience', 'Data Breach "
            "Notification'), independent of the document's section numbering.\n"
            "- 'text' is the exact clause text from the excerpt.\n"
            "- 'chunk_id' is the chunk_id the text came from.\n"
            "- Only include clauses actually present in the excerpts.\n\n"
            f"EXCERPTS:\n{context}"
        )
        inventory = await self._astructured(ClauseInventory, prompt)
        items = self._reconcile_inventory(inventory.items, chunks)
        logger.info("Extracted %d clauses from %s", len(items), document_role)
        return items

    @staticmethod
    def _cap_for_inventory(
        chunks: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        """Cap the chunks fed to one inventory call to a free-tier token budget.

        Args:
            chunks: All chunks of the document.

        Returns:
            A prefix of ``chunks`` whose combined text stays under
            :data:`XREF_INVENTORY_MAX_CHARS`.
        """
        capped: list[RetrievedChunk] = []
        total = 0
        for chunk in chunks:
            capped.append(chunk)
            total += len(chunk.text)
            if total >= XREF_INVENTORY_MAX_CHARS:
                break
        return capped

    @staticmethod
    def _reconcile_inventory(
        items: list[ClauseInventoryItem], chunks: list[RetrievedChunk]
    ) -> list[ClauseInventoryItem]:
        """Reconcile inventory provenance against the real chunks.

        The model can cite a wrong/absent ``chunk_id``; we authoritatively map
        each item to a real chunk (by id, else by text containment) and take the
        page number from that chunk so provenance cannot be hallucinated.

        Args:
            items: Raw inventory items from the model.
            chunks: The chunks supplied as context.

        Returns:
            Items with reconciled ``chunk_id`` / ``page_number``.
        """
        by_id = {c.chunk_id: c for c in chunks}
        reconciled: list[ClauseInventoryItem] = []
        for item in items:
            match = by_id.get(item.chunk_id)
            if match is None:
                snippet = item.text.strip()[:40]
                match = next(
                    (c for c in chunks if snippet and snippet in c.text), None
                )
            if match is not None:
                item.chunk_id = match.chunk_id
                item.page_number = match.page_number
            reconciled.append(item)
        return reconciled

    # --- Phases 2 + 3: align and classify subject clauses --------------------

    async def _classify_subject_clauses(
        self,
        *,
        tenant_id: str,
        standard_document_id: str,
        subject_inventory: list[ClauseInventoryItem],
    ) -> list[ClauseDeviation]:
        """Align each subject clause to the standard and classify the deviation.

        Args:
            tenant_id: The owning tenant.
            standard_document_id: The standard to align against.
            subject_inventory: Normalized subject clauses.

        Returns:
            One :class:`ClauseDeviation` per subject clause.
        """
        semaphore = asyncio.Semaphore(XREF_MAX_CONCURRENCY)

        async def handle(clause: ClauseInventoryItem) -> ClauseDeviation:
            match = self._align_to_standard(
                tenant_id, standard_document_id, clause
            )
            if match is None:
                # Subject has a clause the standard does not cover.
                return ClauseDeviation(
                    clause_type=clause.clause_type,
                    subject_text=clause.text,
                    subject_chunk_id=clause.chunk_id,
                    subject_page=clause.page_number,
                    standard_text=None,
                    standard_chunk_id=None,
                    standard_page=None,
                    deviation_type=DeviationType.UNADDRESSED,
                    severity=_UNADDRESSED_DEFAULT_SEVERITY,
                    explanation=(
                        "This clause type was found in the contract but has no "
                        "counterpart in the corporate standard."
                    ),
                )
            async with semaphore:
                return await self._classify_pair(tenant_id, clause, match)

        # Resilient gather: one clause failing (e.g. retries exhausted on a flaky
        # free tier) must not discard the whole audit — log and skip it.
        results = await asyncio.gather(
            *(handle(c) for c in subject_inventory), return_exceptions=True
        )
        deviations: list[ClauseDeviation] = []
        for clause, result in zip(subject_inventory, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning(
                    "Classification failed for clause '%s'; skipping: %s",
                    clause.clause_type,
                    result,
                )
                continue
            deviations.append(result)
        return deviations

    def _align_to_standard(
        self,
        tenant_id: str,
        standard_document_id: str,
        clause: ClauseInventoryItem,
    ) -> RetrievedChunk | None:
        """Hybrid-search the standard for a subject clause's counterpart.

        Args:
            tenant_id: The owning tenant.
            standard_document_id: The standard to search.
            clause: The subject clause to align.

        Returns:
            The best-matching standard chunk, or ``None`` if nothing clears
            :data:`XREF_MIN_RRF_SCORE`.
        """
        query = f"{clause.clause_type}: {clause.text}"
        vector = self._store.query_standards(
            tenant_id,
            query,
            standard_document_id=standard_document_id,
            top_k=XREF_PER_RETRIEVER_TOP_K,
        )
        keyword = self._store.bm25_query_standards(
            tenant_id,
            query,
            standard_document_id=standard_document_id,
            top_k=XREF_PER_RETRIEVER_TOP_K,
        )
        fused = reciprocal_rank_fusion_scored([vector, keyword])
        if not fused:
            return None
        top_chunk, top_score = fused[0]
        return top_chunk if top_score >= XREF_MIN_RRF_SCORE else None

    async def _classify_pair(
        self, tenant_id: str, subject: ClauseInventoryItem, standard: RetrievedChunk
    ) -> ClauseDeviation:
        """Classify the deviation between an aligned subject/standard clause pair.

        If both sides are tables, a deterministic cell diff is computed first and
        handed to the LLM so it *explains* the diff rather than hallucinating a
        table comparison. Mixed table/prose pairs are flagged as a structural
        difference; prose pairs use the plain comparison.

        Args:
            tenant_id: The owning tenant (for element-metadata lookup).
            subject: The subject clause.
            standard: The aligned standard chunk.

        Returns:
            The classified :class:`ClauseDeviation` (provenance reconciled).
        """
        subject_meta = self._store.get_element_metadata(tenant_id, subject.chunk_id)
        standard_meta = self._store.get_element_metadata(
            tenant_id, standard.chunk_id, kind="standards"
        )
        subject_is_table = subject_meta.get("element_type") == "table"
        standard_is_table = standard_meta.get("element_type") == "table"

        prompt = self._pair_prompt(
            subject,
            standard,
            subject_is_table=subject_is_table,
            standard_is_table=standard_is_table,
            subject_meta=subject_meta,
            standard_meta=standard_meta,
        )
        deviation = await self._astructured(ClauseDeviation, prompt)
        # Authoritative provenance from our own data, not the model.
        deviation.clause_type = subject.clause_type
        deviation.subject_text = subject.text
        deviation.subject_chunk_id = subject.chunk_id
        deviation.subject_page = subject.page_number
        deviation.standard_text = standard.text
        deviation.standard_chunk_id = standard.chunk_id
        deviation.standard_page = standard.page_number
        return deviation

    _DEVIATION_RULES = (
        "deviation_type must be exactly one of:\n"
        "- weakened: subject reduces an obligation / liability cap / protection "
        "relative to the standard.\n"
        "- strengthened: subject increases an obligation beyond the standard "
        "(favourable; flag for negotiation).\n"
        "- contradictory: subject directly conflicts with the standard.\n"
        "- unaddressed: the two texts are not actually about the same clause "
        "(retrieval mismatch).\n"
        "If the clauses are materially equivalent, use 'strengthened' with "
        "severity 1.\n"
        "severity is 1 (trivial) to 10 (critical). 'explanation' must cite the "
        "specific difference.\n"
    )

    def _pair_prompt(
        self,
        subject: ClauseInventoryItem,
        standard: RetrievedChunk,
        *,
        subject_is_table: bool,
        standard_is_table: bool,
        subject_meta: dict[str, object],
        standard_meta: dict[str, object],
    ) -> str:
        """Build the classification prompt for an aligned pair.

        Args:
            subject: The subject clause.
            standard: The aligned standard chunk.
            subject_is_table: Whether the subject element is a table.
            standard_is_table: Whether the standard element is a table.
            subject_meta: Subject element metadata (headers / cells).
            standard_meta: Standard element metadata (headers / cells).

        Returns:
            A prompt tailored to table / mixed / prose pairs.
        """
        header = (
            "Compare a SUBJECT contract clause against the corresponding "
            f"CORPORATE STANDARD clause and classify the deviation.\n\n"
            f"{self._DEVIATION_RULES}"
            f"clause_type: {subject.clause_type}\n\n"
        )
        if subject_is_table and standard_is_table:
            cell_diff = self._diff_tables(subject_meta, standard_meta)
            return (
                header
                + "Both sources are TABLES. A pre-computed CELL DIFF is provided; "
                "base your classification and explanation on it and do not invent "
                "comparisons not present in the diff.\n\n"
                f"CELL DIFF:\n{cell_diff}\n\n"
                f"SUBJECT TABLE:\n{subject.text}\n\n"
                f"STANDARD TABLE:\n{standard.text}"
            )
        if subject_is_table != standard_is_table:
            subj_kind = "a table" if subject_is_table else "prose"
            std_kind = "a table" if standard_is_table else "prose"
            return (
                header
                + f"NOTE: the subject is {subj_kind} and the standard is "
                f"{std_kind}. Flag this structural format difference in the "
                "explanation.\n\n"
                f"SUBJECT CLAUSE:\n{subject.text}\n\n"
                f"STANDARD CLAUSE:\n{standard.text}"
            )
        return (
            header
            + f"SUBJECT CLAUSE:\n{subject.text}\n\n"
            f"STANDARD CLAUSE:\n{standard.text}"
        )

    @staticmethod
    def _diff_tables(
        subject_meta: dict[str, object], standard_meta: dict[str, object]
    ) -> str:
        """Compute a deterministic cell-level diff between two tables.

        Flags numeric cells where the subject exceeds the standard (possible
        weakening) and columns present in the standard but missing from the
        subject. Column alignment is by header label (case-insensitive); when
        headers are absent it falls back to positional comparison.

        Args:
            subject_meta: Subject table metadata (``column_headers`` / cells).
            standard_meta: Standard table metadata.

        Returns:
            A human-readable diff summary for the LLM to explain.
        """
        s_headers = cast("list[str]", subject_meta.get("column_headers") or [])
        t_headers = cast("list[str]", standard_meta.get("column_headers") or [])
        s_data = cast("list[list[str]]", subject_meta.get("structured_data") or [])
        t_data = cast("list[list[str]]", standard_meta.get("structured_data") or [])
        s_lower = [h.lower() for h in s_headers]
        lines: list[str] = []

        for col in t_headers:
            if col.lower() not in s_lower:
                lines.append(
                    f"Column '{col}' is in the standard but missing from the "
                    "contract."
                )

        for t_index, col in enumerate(t_headers):
            if col.lower() not in s_lower:
                continue
            s_index = s_lower.index(col.lower())
            for row in range(min(len(s_data), len(t_data))):
                s_cell = s_data[row][s_index] if s_index < len(s_data[row]) else ""
                t_cell = t_data[row][t_index] if t_index < len(t_data[row]) else ""
                s_num, t_num = _as_number(s_cell), _as_number(t_cell)
                if s_num is not None and t_num is not None and s_num > t_num:
                    lines.append(
                        f"Column '{col}' row {row + 1}: contract {s_cell} > "
                        f"standard {t_cell} (possible weakening)."
                    )
                elif s_cell != t_cell:
                    lines.append(
                        f"Column '{col}' row {row + 1}: contract '{s_cell}' vs "
                        f"standard '{t_cell}'."
                    )

        if not s_headers and not t_headers:
            lines.append(
                "Neither table has detected headers; compare cells positionally."
            )
        return "\n".join(lines) if lines else "No differing cells detected."

    # --- Phase 2b: missing standard clauses ----------------------------------

    def _detect_missing(
        self,
        *,
        tenant_id: str,
        subject_document_id: str,
        standard_inventory: list[ClauseInventoryItem],
    ) -> list[ClauseDeviation]:
        """Flag standard clauses with no counterpart in the subject as MISSING.

        Args:
            tenant_id: The owning tenant.
            subject_document_id: The contract searched for each standard clause.
            standard_inventory: Normalized standard clauses.

        Returns:
            One MISSING :class:`ClauseDeviation` per absent standard clause.
        """
        missing: list[ClauseDeviation] = []
        for clause in standard_inventory:
            query = f"{clause.clause_type}: {clause.text}"
            vector = self._store.query(
                tenant_id,
                query,
                top_k=XREF_PER_RETRIEVER_TOP_K,
                document_ids=[subject_document_id],
            )
            keyword = self._store.bm25_query(
                tenant_id,
                query,
                top_k=XREF_PER_RETRIEVER_TOP_K,
                document_ids=[subject_document_id],
            )
            fused = reciprocal_rank_fusion_scored([vector, keyword])
            top_score = fused[0][1] if fused else 0.0
            if top_score >= XREF_MIN_RRF_SCORE:
                continue  # the contract addresses this clause; handled elsewhere
            missing.append(
                ClauseDeviation(
                    clause_type=clause.clause_type,
                    subject_text="Not present in the subject contract.",
                    subject_chunk_id="",
                    subject_page=None,
                    standard_text=clause.text,
                    standard_chunk_id=clause.chunk_id,
                    standard_page=clause.page_number,
                    deviation_type=DeviationType.MISSING,
                    severity=_MISSING_DEFAULT_SEVERITY,
                    explanation=(
                        "The corporate standard requires this clause, but no "
                        "equivalent was found in the contract."
                    ),
                )
            )
        return missing

    # --- Phase 4: scoring + summary ------------------------------------------

    @staticmethod
    def _compute_risk_score(deviations: list[ClauseDeviation]) -> int:
        """Compute the overall risk score programmatically.

        Args:
            deviations: All detected deviations.

        Returns:
            A 1-10 score: severity-weighted average over deviations, floored at 1.
        """
        if not deviations:
            return 1
        weighted = sum(
            d.severity * DEVIATION_WEIGHTS[d.deviation_type] for d in deviations
        )
        return max(1, min(10, round(weighted / len(deviations))))

    async def _executive_summary(
        self, deviations: list[ClauseDeviation], overall_risk_score: int
    ) -> str:
        """Generate a short, non-legal-stakeholder summary of the findings.

        Only the deviation metadata (not full clause texts) is sent, to keep the
        token footprint small.

        Args:
            deviations: All detected deviations.
            overall_risk_score: The computed overall risk.

        Returns:
            A 3-5 sentence summary.
        """
        if not deviations:
            return (
                "No material deviations from the corporate standard were "
                "detected in this contract."
            )
        lines = "\n".join(
            f"- {d.clause_type}: {d.deviation_type.value} (severity {d.severity})"
            for d in deviations
        )
        prompt = (
            "Write a 3-5 sentence executive summary for non-legal stakeholders "
            f"of a contract review. Overall risk score: {overall_risk_score}/10. "
            "Summarize the most material deviations and the headline risk; do not "
            "list every item.\n\n"
            f"DEVIATIONS:\n{lines}"
        )
        try:
            return await self._ainvoke_text(prompt)
        except Exception as exc:  # noqa: BLE001 - summary must never sink the audit
            logger.warning("Executive summary failed; using fallback: %s", exc)
            return self._fallback_summary(deviations, overall_risk_score)

    @staticmethod
    def _fallback_summary(
        deviations: list[ClauseDeviation], overall_risk_score: int
    ) -> str:
        """Deterministic summary used when the LLM summary call fails.

        The clause analysis is the expensive, valuable part; a summary hiccup
        must not discard it. This reports the same headline figures from data.

        Args:
            deviations: All detected deviations.
            overall_risk_score: The computed overall risk.

        Returns:
            A plain-language summary built from deviation counts.
        """
        counts: dict[str, int] = {}
        for d in deviations:
            counts[d.deviation_type.value] = counts.get(d.deviation_type.value, 0) + 1
        breakdown = ", ".join(f"{n} {kind}" for kind, n in sorted(counts.items()))
        return (
            f"Reviewed against the corporate standard: {len(deviations)} "
            f"deviation(s) found ({breakdown}). Overall risk score "
            f"{overall_risk_score}/10. Review the highest-severity items first."
        )

    # --- LLM plumbing --------------------------------------------------------

    async def _astructured(
        self, schema: type[_StructuredT], prompt: str
    ) -> _StructuredT:
        """Run a structured-output LLM call off the event loop, with retries.

        Args:
            schema: The Pydantic model to bind via ``with_structured_output``.
            prompt: The prompt.

        Returns:
            An instance of ``schema``.
        """
        return await asyncio.to_thread(self._invoke_structured, schema, prompt)

    def _invoke_structured(
        self, schema: type[_StructuredT], prompt: str
    ) -> _StructuredT:
        """Synchronous structured-output call wrapped in the shared retry policy.

        Args:
            schema: The Pydantic model to bind.
            prompt: The prompt.

        Returns:
            An instance of ``schema``.
        """

        def call() -> _StructuredT:
            try:
                result = self._llm.with_structured_output(schema).invoke(prompt)
            except Exception as exc:  # noqa: BLE001 - re-classified
                if _is_retryable(exc):
                    logger.warning("Retryable LLM error (xref): %s", exc)
                    raise RateLimitError(str(exc)) from exc
                raise
            return cast(_StructuredT, result)

        return build_retrying(self._settings.llm_max_retries)(call)

    async def _ainvoke_text(self, prompt: str) -> str:
        """Run a plain-text LLM call off the event loop, with retries.

        Args:
            prompt: The prompt.

        Returns:
            The model's text response.
        """
        return await asyncio.to_thread(self._invoke_text, prompt)

    def _invoke_text(self, prompt: str) -> str:
        """Synchronous text call wrapped in the shared retry policy.

        Args:
            prompt: The prompt.

        Returns:
            The model's text response.
        """

        def call() -> str:
            try:
                message = self._llm.invoke(prompt)
            except Exception as exc:  # noqa: BLE001 - re-classified
                if _is_retryable(exc):
                    logger.warning("Retryable LLM error (xref summary): %s", exc)
                    raise RateLimitError(str(exc)) from exc
                raise
            content = message.content
            return content if isinstance(content, str) else str(content)

        return build_retrying(self._settings.llm_max_retries)(call)

    @staticmethod
    def _format_chunks(chunks: list[RetrievedChunk]) -> str:
        """Render chunks as a chunk-id-tagged block for inventory extraction.

        Args:
            chunks: The chunks to format.

        Returns:
            A newline-delimited, citation-tagged context string.
        """
        blocks = []
        for chunk in chunks:
            page = chunk.page_number if chunk.page_number is not None else "?"
            blocks.append(f"[chunk_id={chunk.chunk_id} page={page}]\n{chunk.text}")
        return "\n\n---\n\n".join(blocks)
