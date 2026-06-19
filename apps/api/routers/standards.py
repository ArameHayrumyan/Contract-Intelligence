"""Corporate-standard upload and listing endpoints (cross-reference workflow)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel

from dependencies import TenantIdDep
from rag_core.config import get_settings
from rag_core.security import UploadValidationError, validate_upload
from runtime import ServiceDep, limiter

logger = logging.getLogger("rag_core.api.standards")

router = APIRouter(prefix="/standards", tags=["standards"])


class StandardUploadResponse(BaseModel):
    """Response after registering a standard for ingestion.

    Attributes:
        standard_document_id: Server-assigned id for this version.
        standard_name: Echoed standard name.
        standard_version: Echoed version label.
        status: Initial ingestion status (``queued``).
    """

    standard_document_id: str
    standard_name: str
    standard_version: str
    status: str


class StandardVersion(BaseModel):
    """One version of a standard."""

    standard_document_id: str
    standard_version: str
    status: str
    chunk_count: int | None = None
    error: str | None = None


class StandardGroup(BaseModel):
    """A standard name with all of its uploaded versions."""

    standard_name: str
    versions: list[StandardVersion]


@router.post("", response_model=StandardUploadResponse, status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("10/minute")  # ingestion-triggering endpoint.
async def upload_standard(
    request: Request,  # noqa: ARG001 - required by slowapi key extraction
    tenant_id: TenantIdDep,
    service: ServiceDep,
    standard_name: str = Form(...),
    standard_version: str = Form(...),
    file: UploadFile = File(...),
) -> StandardUploadResponse:
    """Validate and enqueue a corporate-standard PDF (append-only, versioned).

    Args:
        request: The incoming request (rate limiter).
        tenant_id: The caller's tenant.
        service: The application service container.
        standard_name: Human-readable standard name (grouping key).
        standard_version: Version label for this upload.
        file: The uploaded PDF.

    Returns:
        A :class:`StandardUploadResponse`.

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
        validate_upload(
            data=data, filename=file.filename or "standard.pdf", settings=settings
        )
    except UploadValidationError as exc:
        logger.warning("Standard upload rejected (%s): %s", exc.reason, exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"reason": exc.reason, "message": str(exc)},
        ) from exc

    record = service.register_standard_upload(
        tenant_id=tenant_id,
        data=data,
        standard_name=standard_name,
        standard_version=standard_version,
    )
    return StandardUploadResponse(
        standard_document_id=record.standard_document_id,
        standard_name=record.standard_name,
        standard_version=record.standard_version,
        status="queued",
    )


@router.get("", response_model=list[StandardGroup])
async def list_standards(
    tenant_id: TenantIdDep,
    service: ServiceDep,
) -> list[StandardGroup]:
    """List the tenant's standards, grouped by name with all versions.

    Args:
        tenant_id: The caller's tenant.
        service: The application service container.

    Returns:
        Standard groups, each with its versions.
    """
    records = service.list_standards(tenant_id=tenant_id)
    grouped: dict[str, list[StandardVersion]] = {}
    for r in records:
        grouped.setdefault(r.standard_name, []).append(
            StandardVersion(
                standard_document_id=r.standard_document_id,
                standard_version=r.standard_version,
                status=r.status,
                chunk_count=r.chunk_count,
                error=r.error,
            )
        )
    return [
        StandardGroup(standard_name=name, versions=versions)
        for name, versions in sorted(grouped.items())
    ]
