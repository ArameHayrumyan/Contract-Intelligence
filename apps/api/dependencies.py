"""FastAPI dependencies: authentication and tenant resolution.

Today these are a stub API-key check, but the *signatures* are OIDC-ready: when
this is upgraded to OAuth2/OIDC (Auth0/Okta/Azure AD), only the internals of
``get_current_user`` / ``get_tenant_id`` change — router signatures and business
logic stay identical (Architectural Constraint #5 / Section 3.8).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

logger = logging.getLogger("rag_core.api.deps")

#: Demo API-key → (user, tenant) map. Replaced by an IdP lookup at scale.
#: In a real deployment this is never hard-coded; see SCALING_PATH.md (Secrets).
_API_KEYS: dict[str, tuple[str, str]] = {
    "demo-key-tenant-acme": ("demo-user@acme.example", "acme"),
}


@dataclass(frozen=True)
class CurrentUser:
    """The authenticated principal for a request.

    Attributes:
        user_id: Stable user identifier (email/sub claim at scale).
        tenant_id: The tenant the user belongs to.
    """

    user_id: str
    tenant_id: str


async def get_current_user(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> CurrentUser:
    """Resolve the authenticated user from the request.

    Stub implementation: validates an ``X-API-Key`` header against an in-memory
    map. At enterprise scale this becomes an OIDC token validation (verify JWT
    signature, extract ``sub``/``tenant`` claims) without changing this signature.

    Args:
        x_api_key: The API key supplied via the ``X-API-Key`` header.

    Returns:
        The resolved :class:`CurrentUser`.

    Raises:
        HTTPException: 401 if the key is missing or unrecognised.
    """
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header.",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    principal = _API_KEYS.get(x_api_key)
    if principal is None:
        logger.warning("Rejected request: unknown API key")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    user_id, tenant_id = principal
    return CurrentUser(user_id=user_id, tenant_id=tenant_id)


async def get_tenant_id(
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> str:
    """Resolve the active tenant id for the request.

    Tenant scoping is derived from the authenticated principal — never from a
    client-supplied value — so a caller can never address another tenant's data
    (Architectural Constraint #2).

    Args:
        user: The authenticated user (injected).

    Returns:
        The caller's tenant id.
    """
    return user.tenant_id


async def get_actor(
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> str:
    """Resolve the actor identity for activity-log attribution.

    Today this is the API-key principal's ``user_id``; with OIDC it becomes the
    verified ``sub`` claim — without changing this signature.

    Args:
        user: The authenticated user (injected).

    Returns:
        The actor identifier recorded against mutations.
    """
    return user.user_id


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
TenantIdDep = Annotated[str, Depends(get_tenant_id)]
ActorDep = Annotated[str, Depends(get_actor)]
