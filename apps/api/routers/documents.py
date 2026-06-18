"""Document upload and ingestion-status endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel

from dependencies import TenantIdDep
from rag_core.config import get_settings
from rag_core.schemas import DocumentStatus
from rag_core.security import UploadValidationError, validate_upload
from runtime import ServiceDep, limiter
from service import DocumentNotFoundError

logger = logging.getLogger("rag_core.api.documents")

router = APIRouter(prefix="/documents", tags=["documents"])


class UploadResponse(BaseModel):
    """Response returned after a successful upload.

    Attributes:
        document_id: Server-assigned id to poll for status.
        status: Initial ingestion status (``pending``).
        filename: Echoed original filename.
        page_count: Page count detected during validation.
    """

    document_id: str
    status: DocumentStatus
    filename: str
    page_count: int


class StatusResponse(BaseModel):
    """Ingestion-status response.

    Attributes:
        document_id: The document id.
        status: Current lifecycle state.
        chunk_count: Number of persisted chunks (when ready).
        error: Failure detail (when failed).
    """

    document_id: str
    status: DocumentStatus
    chunk_count: int | None = None
    error: str | None = None


@router.post(
    "",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
@limiter.limit("10/minute")  # LLM/OCR-triggering endpoint — cost-abuse guard.
async def upload_document(
    request: Request,  # noqa: ARG001 - required by slowapi key extraction
    tenant_id: TenantIdDep,
    service: ServiceDep,
    file: UploadFile = File(...),
) -> UploadResponse:
    """Validate and enqueue a contract PDF for ingestion.

    The file is fully validated by ``security.validate_upload`` (size cap, MIME
    sniff, page cap) *before* any processing is scheduled.

    Args:
        request: The incoming request (used by the rate limiter).
        tenant_id: The caller's tenant (from auth).
        service: The application service container.
        file: The uploaded PDF.

    Returns:
        An :class:`UploadResponse` with the new document id.

    Raises:
        HTTPException: 422 if validation fails, 400 if the body is unreadable.
    """
    settings = get_settings()
    try:
        data = await file.read()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not read uploaded file.",
        ) from exc

    try:
        page_count = validate_upload(
            data=data, filename=file.filename or "upload.pdf", settings=settings
        )
    except UploadValidationError as exc:
        logger.warning("Upload rejected (%s): %s", exc.reason, exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"reason": exc.reason, "message": str(exc)},
        ) from exc

    record = service.register_upload(
        tenant_id=tenant_id,
        filename=file.filename or "upload.pdf",
        data=data,
        page_count=page_count,
    )
    return UploadResponse(
        document_id=record.document_id,
        status=record.status,
        filename=record.filename,
        page_count=page_count,
    )


@router.get("/{document_id}", response_model=StatusResponse)
async def get_document_status(
    document_id: str,
    tenant_id: TenantIdDep,
    service: ServiceDep,
) -> StatusResponse:
    """Return ingestion status for a tenant's document.

    Args:
        document_id: The document id to poll.
        tenant_id: The caller's tenant.
        service: The application service container.

    Returns:
        A :class:`StatusResponse`.

    Raises:
        HTTPException: 404 if the document is unknown for the tenant.
    """
    try:
        record = service.get_document(tenant_id=tenant_id, document_id=document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {document_id} not found.",
        ) from exc
    return StatusResponse(
        document_id=record.document_id,
        status=record.status,
        chunk_count=record.chunk_count,
        error=record.error,
    )
