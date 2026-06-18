"""Shared runtime accessors and the rate limiter.

Kept separate from ``main`` so routers can import the service dependency and the
limiter without creating an import cycle with the app factory.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from service import ContractService

#: Per-IP rate limiter. Applied to the LLM-triggering endpoints (/documents,
#: /qa) as a cost-abuse guard (Section 6). Default is generous; routers tighten
#: it per-endpoint.
limiter = Limiter(key_func=get_remote_address, default_limits=["120/minute"])


def get_service(request: Request) -> ContractService:
    """Return the process-wide :class:`ContractService` from app state.

    Args:
        request: The incoming request (carries ``app.state``).

    Returns:
        The application's service container.
    """
    service: ContractService = request.app.state.service
    return service


ServiceDep = Annotated[ContractService, Depends(get_service)]
