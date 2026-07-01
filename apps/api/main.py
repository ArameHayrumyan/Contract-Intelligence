"""FastAPI application entrypoint.

This service is the *only* consumer of ``rag_core`` (Architectural Constraint
#1). It wires logging, the request-context middleware, the rate limiter, and the
routers, and constructs the process-wide :class:`ContractService` at startup.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from middleware.request_context import (
    RequestContextMiddleware,
    install_request_id_logging,
)
from rag_core import registry_store
from rag_core.config import (
    ConfigurationError,
    async_database_url,
    configure_logging as _configure_logging,
    get_settings,
    sync_database_url,
)
from rag_core.database import dispose_db, init_db

# ``annotations`` is aliased because a bare import collides with
# ``from __future__ import annotations`` (which binds the name as a feature).
from routers import (
    activity,
    annotations as annotations_router,
    audit,
    crossref,
    dashboard,
    documents,
    exports,
    monitoring,
    qa,
    standards,
)
from runtime import limiter
from service import ContractService

logger = logging.getLogger("rag_core.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: build the service on startup, tear it down on exit.

    Args:
        app: The FastAPI application.

    Yields:
        Control to the running application.

    Raises:
        ConfigurationError: Propagated if the provider gate rejects the config,
            so an unsafe deployment fails fast at boot.
    """
    settings = get_settings()
    _configure_logging(settings)
    install_request_id_logging()
    logger.info(
        "Starting API: environment=%s provider=%s",
        settings.environment.value,
        settings.llm_provider.value,
    )
    try:
        service = ContractService(settings=settings)
    except ConfigurationError:
        logger.exception("Refusing to start: invalid configuration")
        raise
    app.state.service = service
    # Initialise both persistence layers eagerly (not lazily) so the first
    # request never races table creation: the async audit/compliance store and
    # the sync document/standard registry. Both honour DATABASE_URL (Postgres)
    # and fall back to the local SQLite file.
    await init_db(async_database_url(settings))
    registry_store.init_registry(sync_database_url(settings))
    try:
        yield
    finally:
        service.shutdown()
        await dispose_db()
        registry_store.dispose_registry()
        logger.info("API shutdown complete")


def create_app() -> FastAPI:
    """Application factory.

    Returns:
        A fully-configured :class:`FastAPI` instance.
    """
    app = FastAPI(
        title="Secure Contract Intelligence & SLA Auditor",
        version="2.0.0",
        description="Tenant-scoped contract auditing and cross-document QA.",
        lifespan=lifespan,
    )

    # Rate limiting (Section 6: cost-abuse controls).
    app.state.limiter = limiter
    # slowapi's handler is typed as (Request, RateLimitExceeded) -> Response,
    # narrower than Starlette's (Request, Exception) handler type.
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
    app.add_middleware(SlowAPIMiddleware)

    # Request correlation ids + structured log binding.
    app.add_middleware(RequestContextMiddleware)

    # CORS: the web app is the only intended browser client.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Tighten to the web origin in production deploys.
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    app.include_router(documents.router)
    app.include_router(audit.router)
    app.include_router(qa.router)
    app.include_router(standards.router)
    app.include_router(crossref.router)
    app.include_router(dashboard.router)
    app.include_router(exports.router)
    app.include_router(monitoring.router)
    app.include_router(annotations_router.router, prefix="/documents")
    app.include_router(activity.router)

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        """Liveness probe.

        Returns:
            A static OK payload with environment/provider for quick diagnostics.
        """
        settings = get_settings()
        return {
            "status": "ok",
            "environment": settings.environment.value,
            "provider": settings.llm_provider.value,
        }

    return app


app = create_app()
