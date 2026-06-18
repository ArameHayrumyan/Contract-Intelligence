"""Request-context middleware: correlation ids + structured log binding.

Generates (or honours an inbound) request id, binds it to the logging context
via a :class:`contextvars.ContextVar` so every log line emitted while handling
the request carries ``req=<id>``, and echoes it back in the
``X-Request-ID`` response header. This is the foundation for distributed tracing
later without committing to a full OpenTelemetry stack now (Section 3.8).
"""

from __future__ import annotations

import contextvars
import logging
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

#: Header used both for inbound correlation and outbound echo.
REQUEST_ID_HEADER = "X-Request-ID"

#: Holds the current request id for the duration of a request.
_request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)


def get_request_id() -> str:
    """Return the request id bound to the current context (or ``"-"``)."""
    return _request_id_ctx.get()


class RequestIdLogFilter(logging.Filter):
    """Logging filter that injects the current request id onto each record."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Attach ``request_id`` to ``record`` and always allow it through.

        Args:
            record: The log record being emitted.

        Returns:
            Always ``True`` (this filter never drops records).
        """
        record.request_id = _request_id_ctx.get()
        return True


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Binds a request id to the log context and response headers."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Establish request-id context around the downstream handler.

        Args:
            request: The incoming request.
            call_next: The downstream ASGI handler.

        Returns:
            The response, with the ``X-Request-ID`` header set.
        """
        inbound = request.headers.get(REQUEST_ID_HEADER)
        request_id = inbound or uuid.uuid4().hex
        token = _request_id_ctx.set(request_id)
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        finally:
            _request_id_ctx.reset(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


def install_request_id_logging() -> None:
    """Attach :class:`RequestIdLogFilter` to the ``rag_core`` logger tree.

    Safe to call repeatedly; it will not stack duplicate filters.
    """
    logger = logging.getLogger("rag_core")
    if not any(isinstance(f, RequestIdLogFilter) for f in logger.filters):
        logger.addFilter(RequestIdLogFilter())
