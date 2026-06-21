"""Compliance activity-log read endpoints (the log itself is append-only)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query

from dependencies import TenantIdDep
from rag_core.database import list_activity

logger = logging.getLogger("rag_core.api.activity")

router = APIRouter(tags=["activity"])


@router.get("/activity")
async def get_activity(
    tenant_id: TenantIdDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    """Return the tenant-wide activity log, newest first, paginated.

    Args:
        tenant_id: The caller's tenant.
        page: 1-based page number.
        page_size: Rows per page (max 100).

    Returns:
        ``{items, total, page, page_size, total_pages}``.
    """
    return await list_activity(tenant_id, page=page, page_size=page_size)


@router.get("/documents/{document_id}/activity")
async def get_document_activity(
    document_id: str, tenant_id: TenantIdDep
) -> dict[str, Any]:
    """Return one document's activity, newest first (bounded — no pagination).

    Args:
        document_id: The document.
        tenant_id: The caller's tenant.

    Returns:
        ``{items, total, page, page_size, total_pages}`` (single page).
    """
    return await list_activity(
        tenant_id, document_id=document_id, page=1, page_size=1000
    )
