"""RAG engine: multi-query expansion, Reciprocal Rank Fusion, structured output.

The audit pipeline expands a single intent into three semantic variants
(compliance, financial obligations, termination mechanics), retrieves per
variant, fuses the ranked lists with RRF, and feeds the top fused chunks into a
structured-output LLM call bound to :class:`ContractAuditSchema`.

All LLM calls go through :mod:`tenacity` retry logic, which matters because dev
runs on a free tier that will genuinely rate-limit (Section 3.6).
"""

from __future__ import annotations

import logging
from collections import defaultdict

from langchain_core.language_models.chat_models import BaseChatModel
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from rag_core.config import Settings
from rag_core.schemas import (
    ContractAuditSchema,
    QACitation,
    QAResponse,
)
from rag_core.storage import RetrievedChunk, TenantVectorStore

logger = logging.getLogger("rag_core.engine")

#: RRF damping constant (Section 3.6 specifies k=60).
RRF_K = 60
#: Chunks retrieved per query variant.
PER_VARIANT_TOP_K = 10
#: Fused chunks fed to the generator.
FUSED_TOP_K = 5

#: Semantic variants steering retrieval toward the three audit dimensions.
_AUDIT_QUERY_VARIANTS: tuple[str, ...] = (
    "compliance obligations, regulatory requirements, audit rights, data "
    "protection, confidentiality and indemnification clauses",
    "financial obligations: fees, payment terms, liability caps, penalties, "
    "service level credits and price escalation",
    "termination mechanics: term length, auto-renewal, notice period, "
    "termination for cause or convenience, and exit obligations",
)


class RateLimitError(RuntimeError):
    """Normalised rate-limit signal that triggers tenacity retries."""


def _is_retryable(exc: BaseException) -> bool:
    """Heuristically classify an exception as a transient rate-limit/5xx.

    Provider SDKs raise different exception types; rather than import them all we
    match on the normalised message, plus our own :class:`RateLimitError`.

    Args:
        exc: The exception raised by an LLM call.

    Returns:
        ``True`` if the call should be retried.
    """
    if isinstance(exc, RateLimitError):
        return True
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(
        token in text
        for token in (
            "rate limit",
            "429",
            "overloaded",
            "503",
            "timeout",
            "too many",
            # Smaller models intermittently emit a malformed tool/function call
            # that the provider rejects; a retry often succeeds.
            "tool_use_failed",
            "failed to call a function",
        )
    )


def build_retrying(max_retries: int) -> Retrying:
    """Build the shared retry policy for transient (rate-limit / 5xx) LLM errors.

    Backoff is deliberately patient: free tiers enforce a per-minute token window
    (e.g. Groq's 6k TPM), so a burst can stay rate-limited for most of a 60s
    window. Capping the wait at 60s over ``max_retries`` attempts lets a single
    call outlast a saturated window instead of failing. Only
    :class:`RateLimitError` is retried — non-retryable errors surface immediately.

    Both :class:`AuditEngine` and the cross-reference engine use this one policy.

    Args:
        max_retries: Number of retries (total attempts is this + 1).

    Returns:
        A configured :class:`tenacity.Retrying`.
    """
    return Retrying(
        retry=retry_if_exception_type(RateLimitError),
        stop=stop_after_attempt(max_retries + 1),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        reraise=True,
    )


def reciprocal_rank_fusion_scored(
    ranked_lists: list[list[RetrievedChunk]], *, k: int = RRF_K
) -> list[tuple[RetrievedChunk, float]]:
    """Fuse several ranked chunk lists via RRF, returning the fused scores.

    Each chunk's fused score is ``sum(1 / (k + rank))`` over the lists it appears
    in (rank is 0-based). Duplicate chunk ids are merged. This is the single
    implementation of the algorithm; :func:`reciprocal_rank_fusion` drops the
    scores, and the cross-reference engine uses the scores to threshold whether a
    clause has a counterpart in the standard.

    Args:
        ranked_lists: One ranked list of chunks per query variant.
        k: RRF damping constant.

    Returns:
        ``(chunk, fused_score)`` pairs sorted by descending fused score.
    """
    scores: dict[str, float] = defaultdict(float)
    representative: dict[str, RetrievedChunk] = {}

    for ranked in ranked_lists:
        for rank, chunk in enumerate(ranked):
            scores[chunk.chunk_id] += 1.0 / (k + rank)
            # Keep the closest-distance representative for the chunk.
            existing = representative.get(chunk.chunk_id)
            if existing is None or chunk.distance < existing.distance:
                representative[chunk.chunk_id] = chunk

    ordered_ids = sorted(scores, key=lambda cid: scores[cid], reverse=True)
    return [(representative[cid], scores[cid]) for cid in ordered_ids]


def reciprocal_rank_fusion(
    ranked_lists: list[list[RetrievedChunk]], *, k: int = RRF_K
) -> list[RetrievedChunk]:
    """Fuse several ranked chunk lists via Reciprocal Rank Fusion.

    Thin wrapper over :func:`reciprocal_rank_fusion_scored` that drops the
    scores. Behaviour is unchanged for existing callers.

    Args:
        ranked_lists: One ranked list of chunks per query variant.
        k: RRF damping constant.

    Returns:
        Chunks sorted by descending fused score.
    """
    return [chunk for chunk, _ in reciprocal_rank_fusion_scored(ranked_lists, k=k)]


class AuditEngine:
    """Generates structured contract audits and answers cross-document QA."""

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
            store: Tenant-scoped vector store.
            llm: The chat model resolved by ``LLMProviderFactory``.
        """
        self._settings = settings
        self._store = store
        self._llm = llm

    # --- Retrieval -----------------------------------------------------------

    def _fused_retrieval(
        self,
        tenant_id: str,
        variants: tuple[str, ...],
        *,
        document_ids: list[str] | None = None,
    ) -> list[RetrievedChunk]:
        """Retrieve per variant via hybrid search and fuse with RRF.

        Each variant contributes two ranked lists — semantic (vector) and lexical
        (BM25) — so RRF fuses ``2 * len(variants)`` lists (6 for the standard 3
        variants). BM25 rescues exact references (article numbers, defined terms,
        identifiers like ``Section 4.1.a``) that vector similarity smooths away;
        the vector side keeps recall on paraphrased / semantically-stated terms.
        The fusion function and ``k=60`` are unchanged.

        Args:
            tenant_id: The tenant whose collection to search.
            variants: Query variant strings.
            document_ids: Optional document subset.

        Returns:
            Top ``FUSED_TOP_K`` fused chunks.
        """
        ranked_lists: list[list[RetrievedChunk]] = []
        for variant in variants:
            ranked_lists.append(
                self._store.query(
                    tenant_id,
                    variant,
                    top_k=PER_VARIANT_TOP_K,
                    document_ids=document_ids,
                )
            )
            ranked_lists.append(
                self._store.bm25_query(
                    tenant_id,
                    variant,
                    top_k=PER_VARIANT_TOP_K,
                    document_ids=document_ids,
                )
            )
        fused = reciprocal_rank_fusion(ranked_lists)[:FUSED_TOP_K]
        logger.info(
            "Hybrid retrieval tenant=%s variants=%d lists=%d fused=%d",
            tenant_id,
            len(variants),
            len(ranked_lists),
            len(fused),
        )
        return fused

    # --- Audit ---------------------------------------------------------------

    def audit_document(
        self, *, tenant_id: str, document_id: str
    ) -> ContractAuditSchema:
        """Produce a structured audit for a single document.

        Args:
            tenant_id: The owning tenant.
            document_id: The document to audit.

        Returns:
            A populated :class:`ContractAuditSchema`, with provenance on each
            critical clause.

        Raises:
            ValueError: If no chunks are found for the document.
        """
        chunks = self._fused_retrieval(
            tenant_id, _AUDIT_QUERY_VARIANTS, document_ids=[document_id]
        )
        if not chunks:
            raise ValueError(
                f"No indexed content for document={document_id}; cannot audit."
            )

        context = self._format_context(chunks)
        prompt = self._audit_prompt(context)
        audit = self._generate_structured(prompt)
        audit = self._repair_provenance(audit, chunks)
        logger.info(
            "Audit complete tenant=%s document=%s risk=%d clauses=%d",
            tenant_id,
            document_id,
            audit.risk_score,
            len(audit.critical_clauses),
        )
        return audit

    def _retrying(self) -> Retrying:
        """Return the shared transient-error retry policy.

        Returns:
            A configured :class:`tenacity.Retrying`.
        """
        return build_retrying(self._settings.llm_max_retries)

    def _generate_structured(self, prompt: str) -> ContractAuditSchema:
        """Invoke the LLM with structured-output binding, with retries.

        Args:
            prompt: The fully-formed audit prompt.

        Returns:
            The parsed :class:`ContractAuditSchema`.

        Raises:
            Exception: Re-raised after the retry budget is exhausted, or
                immediately for non-retryable errors.
        """
        return self._retrying()(self._invoke_structured, prompt)

    def _invoke_structured(self, prompt: str) -> ContractAuditSchema:
        """Single structured-output LLM call (one retry attempt).

        Args:
            prompt: The fully-formed audit prompt.

        Returns:
            The parsed :class:`ContractAuditSchema`.

        Raises:
            RateLimitError: For transient failures (triggers a retry).
            Exception: Immediately for non-retryable errors.
        """
        try:
            structured = self._llm.with_structured_output(ContractAuditSchema)
            result = structured.invoke(prompt)
        except Exception as exc:  # noqa: BLE001 - re-classified below
            if _is_retryable(exc):
                logger.warning("Retryable LLM error: %s", exc)
                raise RateLimitError(str(exc)) from exc
            logger.exception("Non-retryable LLM error during structured generation")
            raise
        if not isinstance(result, ContractAuditSchema):  # pragma: no cover - defensive
            raise TypeError(f"Expected ContractAuditSchema, got {type(result)!r}")
        return result

    @staticmethod
    def _repair_provenance(
        audit: ContractAuditSchema, chunks: list[RetrievedChunk]
    ) -> ContractAuditSchema:
        """Backfill page numbers for clauses whose chunk id is in the context.

        The model is asked to cite ``source_chunk_id``; we authoritatively set
        ``page_number`` from our own retrieval metadata so provenance can never
        be hallucinated.

        Args:
            audit: The model's audit output.
            chunks: The chunks supplied as context.

        Returns:
            The audit with reconciled provenance.
        """
        by_id = {c.chunk_id: c for c in chunks}
        for clause in audit.critical_clauses:
            source = by_id.get(clause.source_chunk_id)
            if source is not None:
                clause.page_number = source.page_number
        return audit

    # --- QA ------------------------------------------------------------------

    def answer_question(
        self,
        *,
        tenant_id: str,
        question: str,
        document_ids: list[str] | None = None,
    ) -> QAResponse:
        """Answer a cross-document question, scoped to the tenant.

        Args:
            tenant_id: The owning tenant.
            question: The user's natural-language question.
            document_ids: Optional document subset to restrict the search to.

        Returns:
            A :class:`QAResponse` with the answer and supporting citations.
        """
        variants = self._expand_question(question)
        chunks = self._fused_retrieval(
            tenant_id, variants, document_ids=document_ids
        )
        if not chunks:
            return QAResponse(
                answer="No relevant content was found in your documents.",
                citations=[],
            )

        context = self._format_context(chunks)
        answer = self._generate_answer(question, context)
        citations = [
            QACitation(
                chunk_id=c.chunk_id,
                document_id=c.document_id,
                page_number=c.page_number,
                snippet=c.text[:280],
            )
            for c in chunks
        ]
        return QAResponse(answer=answer, citations=citations)

    def _generate_answer(self, question: str, context: str) -> str:
        """Invoke the LLM for a grounded free-text answer, with retries.

        Args:
            question: The user's question.
            context: The formatted, citation-tagged context.

        Returns:
            The answer text.
        """
        return self._retrying()(self._invoke_answer, question, context)

    def _invoke_answer(self, question: str, context: str) -> str:
        """Single free-text LLM call (one retry attempt).

        Args:
            question: The user's question.
            context: The formatted, citation-tagged context.

        Returns:
            The answer text.

        Raises:
            RateLimitError: For transient failures (triggers a retry).
        """
        prompt = (
            "You are a contract analyst. Answer the QUESTION using ONLY the "
            "CONTEXT. Cite chunk ids inline like [chunk_id]. If the answer is "
            "not in the context, say so explicitly.\n\n"
            f"CONTEXT:\n{context}\n\nQUESTION: {question}\n\nANSWER:"
        )
        try:
            message = self._llm.invoke(prompt)
        except Exception as exc:  # noqa: BLE001 - re-classified
            if _is_retryable(exc):
                logger.warning("Retryable LLM error (QA): %s", exc)
                raise RateLimitError(str(exc)) from exc
            raise
        content = message.content
        return content if isinstance(content, str) else str(content)

    @staticmethod
    def _expand_question(question: str) -> tuple[str, ...]:
        """Expand a question into three retrieval-friendly variants.

        Args:
            question: The original question.

        Returns:
            Three query strings (verbatim, rephrased, and keyword-focused).
        """
        return (
            question,
            f"Relevant contract clauses and obligations regarding: {question}",
            f"key terms, definitions and exceptions related to {question}",
        )

    # --- Prompting -----------------------------------------------------------

    @staticmethod
    def _format_context(chunks: list[RetrievedChunk]) -> str:
        """Render chunks as a citation-tagged context block.

        Args:
            chunks: The chunks to format.

        Returns:
            A newline-delimited context string, each block tagged with its
            chunk id and page so the model can cite provenance.
        """
        blocks = []
        for chunk in chunks:
            page = chunk.page_number if chunk.page_number is not None else "?"
            blocks.append(
                f"[chunk_id={chunk.chunk_id} page={page}]\n{chunk.text}"
            )
        return "\n\n---\n\n".join(blocks)

    @staticmethod
    def _audit_prompt(context: str) -> str:
        """Build the structured-audit prompt.

        Args:
            context: The formatted context block.

        Returns:
            The full prompt instructing the model to populate the schema and to
            set ``source_chunk_id`` for every critical clause.
        """
        return (
            "You are a senior contracts auditor. Analyse the CONTEXT excerpts of "
            "a single contract and produce a structured audit.\n\n"
            "Rules:\n"
            "- Base every field strictly on the CONTEXT; do not invent terms.\n"
            "- Respect each field's type EXACTLY. Never put text such as "
            "'Not specified' into a boolean or integer field:\n"
            "  * vendor_name, contract_type, liability_cap_description (text): "
            "if absent, use the string 'Not specified'.\n"
            "  * auto_renewal (boolean): true or false only; if not stated, false.\n"
            "  * notice_period_days (integer >= 0): the number of days; if not "
            "stated, 0.\n"
            "  * risk_score (integer 1-10): 1 negligible .. 10 severe; justify in "
            "'risk_rationale'.\n"
            "- For each critical clause, set 'source_chunk_id' to the chunk_id of "
            "the excerpt the clause text came from.\n"
            "- If the document is not actually a contract, still return a valid "
            "audit with the type-correct fallbacks above and a low risk_score.\n\n"
            f"CONTEXT:\n{context}"
        )
