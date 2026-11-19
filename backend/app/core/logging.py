import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings


def configure_logging() -> None:
    logging.basicConfig(
        level=get_settings().log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Adds an X-Request-ID header and logs latency for every request."""

    async def dispatch(self, request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        start = time.perf_counter()
        response = await call_next(request)
        elapsed = (time.perf_counter() - start) * 1000
        response.headers["X-Request-ID"] = request_id
        logging.getLogger("api").info(
            "%s %s -> %s (%.0f ms) rid=%s",
            request.method, request.url.path, response.status_code, elapsed, request_id,
        )
        return response
