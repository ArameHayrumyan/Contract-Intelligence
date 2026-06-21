"""Human annotation endpoints (document / clause / deviation level).

All mutations are recorded in the immutable activity log by the database layer.
Deletes are soft (the row remains, hidden) — there is no hard-delete path.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from dependencies import ActorDep, TenantIdDep
from rag_core.database import (
    create_annotation,
    delete_annotation,
    deviation_exists,
    get_audit_result,
    list_annotations,
    update_annotation,
)
from rag_core.schemas import (
    AnnotationResponse,
    CreateAnnotationRequest,
    UpdateAnnotationRequest,
)
from runtime import ServiceDep

logger = logging.getLogger("rag_core.api.annotations")

router = APIRouter(tags=["annotations"])


async def _require_owned_document(document_id: str, tenant_id: str) -> None:
    """403 if the document has no audit row for this tenant (no info leak)."""
    if await get_audit_result(document_id, tenant_id) is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Document not found for this tenant.",
        )


@router.post(
    "/{document_id}/annotations",
    response_model=AnnotationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create(
    document_id: str,
    payload: CreateAnnotationRequest,
    tenant_id: TenantIdDep,
    actor: ActorDep,
    service: ServiceDep,
) -> AnnotationResponse:
    """Create an annotation on a document, clause, or deviation.

    Args:
        document_id: The annotated document.
        payload: The annotation (validated body).
        tenant_id: The caller's tenant.
        actor: Who recorded the note.
        service: For clause chunk-existence validation.

    Returns:
        The created annotation (201).

    Raises:
        HTTPException: 403 (not owned), 422 (invalid target reference).
    """
    await _require_owned_document(document_id, tenant_id)

    if payload.target_type == "clause":
        if not service.chunk_exists(
            tenant_id=tenant_id, document_id=document_id, chunk_id=payload.target_reference or ""
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"chunk_id {payload.target_reference!r} not found in this document.",
            )
    elif payload.target_type == "deviation" and not await deviation_exists(
        tenant_id, document_id, payload.target_reference or ""
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"deviation_id {payload.target_reference!r} not found for this document.",
        )

    row = await create_annotation(
        tenant_id=tenant_id,
        document_id=document_id,
        target_type=payload.target_type,
        target_reference=payload.target_reference,
        annotation_type=str(payload.annotation_type),
        note=payload.note,
        actor=actor,
    )
    return AnnotationResponse.model_validate(row)


@router.get("/{document_id}/annotations", response_model=list[AnnotationResponse])
async def list_for_document(
    document_id: str,
    tenant_id: TenantIdDep,
    target_type: str | None = None,
    target_reference: str | None = None,
) -> list[AnnotationResponse]:
    """List a document's (non-deleted) annotations, optionally filtered.

    Args:
        document_id: The document.
        tenant_id: The caller's tenant.
        target_type: Optional filter.
        target_reference: Optional filter.

    Returns:
        Matching annotations, newest first.
    """
    await _require_owned_document(document_id, tenant_id)
    rows = await list_annotations(
        tenant_id, document_id, target_type=target_type, target_reference=target_reference
    )
    return [AnnotationResponse.model_validate(r) for r in rows]


@router.patch(
    "/{document_id}/annotations/{annotation_id}",
    response_model=AnnotationResponse,
)
async def edit(
    document_id: str,
    annotation_id: str,
    payload: UpdateAnnotationRequest,
    tenant_id: TenantIdDep,
    actor: ActorDep,
) -> AnnotationResponse:
    """Edit an annotation's type and note.

    Args:
        document_id: The owning document.
        annotation_id: The annotation to edit.
        payload: New type + note.
        tenant_id: The caller's tenant.
        actor: Who edited.

    Returns:
        The updated annotation.

    Raises:
        HTTPException: 404 if the annotation is unknown for this document/tenant.
    """
    await _require_owned_document(document_id, tenant_id)
    row = await update_annotation(
        annotation_id, tenant_id, payload.note, str(payload.annotation_type), actor
    )
    if row is None or row["document_id"] != document_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Annotation not found."
        )
    return AnnotationResponse.model_validate(row)


@router.delete(
    "/{document_id}/annotations/{annotation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove(
    document_id: str,
    annotation_id: str,
    tenant_id: TenantIdDep,
    actor: ActorDep,
) -> None:
    """Soft-delete an annotation (the deletion is still logged).

    Args:
        document_id: The owning document.
        annotation_id: The annotation to delete.
        tenant_id: The caller's tenant.
        actor: Who deleted it.

    Raises:
        HTTPException: 404 if the annotation is unknown for this tenant.
    """
    await _require_owned_document(document_id, tenant_id)
    deleted = await delete_annotation(annotation_id, tenant_id, actor)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Annotation not found."
        )
