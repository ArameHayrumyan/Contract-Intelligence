"""Cross-reference audit endpoint: compare a contract against a standard."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from dependencies import TenantIdDep
from rag_core.schemas_xref import CrossReferenceAuditSchema
from runtime import ServiceDep, limiter
from service import DocumentNotFoundError, StandardNotFoundError

logger = logging.getLogger("rag_core.api.crossref")

router = APIRouter(prefix="/documents", tags=["cross-reference"])


class CrossReferenceRequest(BaseModel):
    """Request body for a cross-reference run.

    Attributes:
        standard_document_id: The standard version to compare the contract against.
    """

    standard_document_id: str


@router.post(
    "/{document_id}/cross-reference",
    response_model=CrossReferenceAuditSchema,
)
@limiter.limit("6/minute")  # multi-phase, LLM-heavy — tightest cost-abuse guard.
async def cross_reference(
    request: Request,  # noqa: ARG001 - required by slowapi key extraction
    document_id: str,
    payload: CrossReferenceRequest,
    tenant_id: TenantIdDep,
    service: ServiceDep,
) -> CrossReferenceAuditSchema:
    """Cross-reference a contract against a corporate standard.

    Both the subject document and the standard must belong to the caller's
    tenant — a tenant cannot cross-reference against another tenant's standard
    even with a valid id (returns 403).

    Synchronous for the demo (see SCALING_PATH.md: the first thing to background
    at production scale).

    Args:
        request: The incoming request (rate limiter).
        document_id: The subject contract.
        payload: The standard to compare against.
        tenant_id: The caller's tenant.
        service: The application service container.

    Returns:
        The :class:`CrossReferenceAuditSchema`.

    Raises:
        HTTPException: 404 (unknown subject), 403 (standard not owned by tenant),
            409 (not ready), 502 (engine/provider failure).
    """
    try:
        return await service.cross_reference(
            tenant_id=tenant_id,
            document_id=document_id,
            standard_document_id=payload.standard_document_id,
        )
    except DocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {document_id} not found.",
        ) from exc
    except StandardNotFoundError as exc:
        # Do not let a tenant reach another tenant's standard.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Standard not found for this tenant.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except Exception as exc:  # noqa: BLE001 - surface engine/provider failures
        logger.exception("Cross-reference failed for document=%s", document_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Cross-reference failed; see server logs.",
        ) from exc
