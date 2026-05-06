import logging
from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    """
    Wraps DRF's default exception handler to enforce a consistent
    error envelope across every endpoint:

    {
        "status": "error",
        "code": 400,
        "message": "Human-readable summary",
        "errors": { ... }   ← field-level detail when available
    }
    """
    # Let DRF handle what it knows
    response = exception_handler(exc, context)

    if response is not None:
        error_payload = {
            "status": "error",
            "code": response.status_code,
            "message": _extract_message(response.data),
            "errors": response.data if isinstance(response.data, dict) else {},
        }

        # Log 5xx as errors, 4xx as warnings
        if response.status_code >= 500:
            logger.error(
                "Server error",
                extra={
                    "status_code": response.status_code,
                    "path": context["request"].path,
                    "errors": response.data,
                },
                exc_info=exc,
            )
        else:
            logger.warning(
                "Client error",
                extra={
                    "status_code": response.status_code,
                    "path": context["request"].path,
                },
            )

        response.data = error_payload
        return response

    # Unhandled exception — return 500
    logger.exception(
        "Unhandled exception",
        extra={"path": context["request"].path},
        exc_info=exc,
    )
    return Response(
        {
            "status": "error",
            "code": 500,
            "message": "An unexpected server error occurred.",
            "errors": {},
        },
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def _extract_message(data) -> str:
    """Pull the most useful human-readable message from DRF error data."""
    if isinstance(data, str):
        return data
    if isinstance(data, list) and data:
        return str(data[0])
    if isinstance(data, dict):
        if "detail" in data:
            return str(data["detail"])
        # Return first field error
        for key, val in data.items():
            if isinstance(val, list) and val:
                return f"{key}: {val[0]}"
            return str(val)
    return "An error occurred."