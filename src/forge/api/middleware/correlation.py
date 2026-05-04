"""Correlation ID middleware for request tracking."""

import logging
import uuid
from collections.abc import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from forge.observability.context import set_correlation_id

logger = logging.getLogger(__name__)

# Header names for correlation ID
CORRELATION_ID_HEADER = "X-Correlation-ID"
REQUEST_ID_HEADER = "X-Request-ID"


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Middleware to inject and propagate correlation IDs.

    Extracts correlation ID from incoming request headers or generates
    a new one. Sets the correlation ID in context for logging and tracing.
    Includes correlation ID in response headers.
    """

    def __init__(self, app: ASGIApp, header_name: str = CORRELATION_ID_HEADER):
        """Initialize the middleware.

        Args:
            app: ASGI application.
            header_name: Header to use for correlation ID.
        """
        super().__init__(app)
        self.header_name = header_name

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        """Process request and add correlation ID.

        Args:
            request: Incoming request.
            call_next: Next middleware/handler.

        Returns:
            Response with correlation ID header.
        """
        # Get existing correlation ID or generate new one
        correlation_id = (
            request.headers.get(self.header_name)
            or request.headers.get(REQUEST_ID_HEADER)
            or str(uuid.uuid4())
        )

        # Set in context for this request
        set_correlation_id(correlation_id)

        # Log the request with correlation ID
        logger.info(f"[{correlation_id[:8]}] {request.method} {request.url.path}")

        # Process request
        response = await call_next(request)

        # Add correlation ID to response
        response.headers[self.header_name] = correlation_id

        return response


def get_correlation_middleware() -> CorrelationIdMiddleware:
    """Factory function for creating the middleware.

    Returns:
        Configured CorrelationIdMiddleware instance.
    """
    return CorrelationIdMiddleware
