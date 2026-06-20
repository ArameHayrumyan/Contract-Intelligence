"""SLA & renewal monitoring endpoints (tenant-scoped)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from dependencies import TenantIdDep
from rag_core.database import (
    get_renewal_alerts,
    get_renewal_thresholds,
    get_unknown_date_autorenewals,
    set_renewal_thresholds,
)

logger = logging.getLogger("rag_core.api.monitoring")

router = APIRouter(prefix="/monitoring", tags=["monitoring"])


class ThresholdUpdate(BaseModel):
    """Body for updating renewal-alert thresholds.

    Attributes:
        thresholds: 1-3 strictly-ascending day values in [1, 365].
    """

    thresholds: list[int]


def _parse_thresholds(raw: str) -> list[int]:
    """Parse a comma-separated threshold string into sorted unique ints.

    Args:
        raw: e.g. ``"30,60,90"``.

    Returns:
        Ascending integer thresholds (falls back to defaults if unparseable).
    """
    try:
        values = sorted({int(part) for part in raw.split(",") if part.strip()})
    except ValueError:
        return [30, 60, 90]
    return values or [30, 60, 90]


@router.get("/renewals")
async def get_renewals(
    tenant_id: TenantIdDep,
    thresholds: str = Query(default="30,60,90"),
    include_no_date: bool = False,
) -> dict[str, Any]:
    """Return auto-renewing contracts grouped into expiry windows.

    Args:
        tenant_id: The caller's tenant.
        thresholds: Comma-separated day windows (1-3 values).
        include_no_date: Also include auto-renewals with no recorded end date.

    Returns:
        ``{windows, total_at_risk, configured_thresholds}`` (plus
        ``unknown_date`` when requested).
    """
    days = _parse_thresholds(thresholds)
    # Cumulative alert sets keyed by threshold; windows are successive diffs.
    cumulative = [await get_renewal_alerts(tenant_id, t) for t in days]
    seen: set[str] = set()
    windows: list[dict[str, Any]] = []
    for threshold, alerts in zip(days, cumulative, strict=True):
        bucket = [a for a in alerts if a["document_id"] not in seen]
        seen.update(a["document_id"] for a in alerts)
        windows.append(
            {
                "label": f"Within {threshold} Days",
                "threshold_days": threshold,
                "count": len(bucket),
                "contracts": bucket,
            }
        )

    total_at_risk = len(seen)
    response: dict[str, Any] = {
        "windows": windows,
        "total_at_risk": total_at_risk,
        "configured_thresholds": days,
    }
    if include_no_date:
        unknown = await get_unknown_date_autorenewals(tenant_id)
        response["unknown_date"] = {
            "label": "Auto-Renewal — End Date Unknown",
            "count": len(unknown),
            "contracts": unknown,
        }
    return response


@router.get("/thresholds")
async def read_thresholds(tenant_id: TenantIdDep) -> dict[str, list[int]]:
    """Return the tenant's configured renewal thresholds (or defaults).

    Args:
        tenant_id: The caller's tenant.

    Returns:
        ``{thresholds: [...]}``.
    """
    return {"thresholds": await get_renewal_thresholds(tenant_id)}


@router.patch("/thresholds")
async def update_thresholds(
    payload: ThresholdUpdate, tenant_id: TenantIdDep
) -> dict[str, list[int]]:
    """Persist new renewal thresholds for the tenant.

    Args:
        payload: 1-3 strictly-ascending thresholds in [1, 365].
        tenant_id: The caller's tenant.

    Returns:
        ``{thresholds: [...]}`` with the saved values.

    Raises:
        HTTPException: 422 if validation fails.
    """
    try:
        saved = await set_renewal_thresholds(tenant_id, payload.thresholds)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return {"thresholds": saved}
