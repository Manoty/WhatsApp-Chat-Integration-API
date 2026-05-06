import time
import logging
import uuid

logger = logging.getLogger(__name__)

# Paths we never want to spam logs with
SKIP_LOGGING_PATHS = {"/api/health/", "/favicon.ico"}


class RequestLoggingMiddleware:
    """
    Logs every HTTP request with:
    - Unique request ID (for tracing across log lines)
    - Method + path + status code
    - Response time in milliseconds
    - Client IP

    Attaches request_id to the request object so views can use it.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path in SKIP_LOGGING_PATHS:
            return self.get_response(request)

        request_id = str(uuid.uuid4())[:8]
        request.request_id = request_id
        start = time.monotonic()

        response = self.get_response(request)

        duration_ms = round((time.monotonic() - start) * 1000, 2)

        log_level = logging.WARNING if response.status_code >= 400 else logging.INFO

        logger.log(
            log_level,
            "%s %s → %s (%sms)",
            request.method,
            request.path,
            response.status_code,
            duration_ms,
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
                "ip": self._get_ip(request),
            },
        )

        # Attach request ID to response headers for client-side tracing
        response["X-Request-ID"] = request_id
        return response

    def _get_ip(self, request) -> str:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "unknown")