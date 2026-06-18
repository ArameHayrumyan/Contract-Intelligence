"""Structured contract-audit endpoint."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from dependencies import TenantIdDep
from rag_core.schemas import ContractAuditSchema
from runtime import ServiceDep
from service import DocumentNotFoundError

logger = logging.getLogger("rag_core.api.audit")

router = APIRouter(prefix="/documents", tags=["audit"])


@router.get("/{document_id}/audit", response_model=ContractAuditSchema)
async def get_audit(
    document_id: str,
    tenant_id: TenantIdDep,
    service: ServiceDep,
) -> ContractAuditSchema:
    """Return the structured audit for a ready document.

    The audit is computed lazily on first request and cached on the document
    record thereafter.

    Args:
        document_id: The document to audit.
        tenant_id: The caller's tenant.
        service: The application service container.

    Returns:
        The :class:`ContractAuditSchema`, including per-clause provenance.

    Raises:
        HTTPException: 404 if unknown, 409 if not yet ready, 502 on engine error.
    """
    try:
        return service.get_audit(tenant_id=tenant_id, document_id=document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {document_id} not found.",
        ) from exc
    except ValueError as exc:
        # Document exists but is not ready (still processing / failed).
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except Exception as exc:  # noqa: BLE001 - surface engine/provider failures
        logger.exception("Audit generation failed for document=%s", document_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Audit generation failed; see server logs.",
        ) from exc
