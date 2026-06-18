"""Cross-document RAG-fusion QA endpoint."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, status

from dependencies import TenantIdDep
from rag_core.schemas import QARequest, QAResponse
from runtime import ServiceDep, limiter

logger = logging.getLogger("rag_core.api.qa")

router = APIRouter(prefix="/qa", tags=["qa"])


@router.post("", response_model=QAResponse)
@limiter.limit("20/minute")  # LLM-triggering endpoint — cost-abuse guard.
async def ask_question(
    request: Request,  # noqa: ARG001 - required by slowapi key extraction
    payload: QARequest,
    tenant_id: TenantIdDep,
    service: ServiceDep,
) -> QAResponse:
    """Answer a question over the caller's tenant document set.

    Retrieval and generation are strictly tenant-scoped; the answer carries
    citations (chunk id + page) for provenance.

    Args:
        request: The incoming request (used by the rate limiter).
        payload: The question and optional document subset.
        tenant_id: The caller's tenant.
        service: The application service container.

    Returns:
        A :class:`QAResponse` with answer and citations.

    Raises:
        HTTPException: 502 if the engine/provider call fails.
    """
    try:
        return service.answer_question(
            tenant_id=tenant_id,
            question=payload.question,
            document_ids=payload.document_ids,
        )
    except Exception as exc:  # noqa: BLE001 - surface engine/provider failures
        logger.exception("QA failed for tenant=%s", tenant_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Question answering failed; see server logs.",
        ) from exc
