import logging
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from django.conf import settings

logger = logging.getLogger(__name__)


class APIKeyUser:
    """
    Lightweight user object returned by APIKeyAuthentication.
    DRF requires an is_authenticated attribute.
    """
    is_authenticated = True

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.pk = api_key  # Required by some DRF internals

    def __str__(self):
        return f"APIKey({self.api_key[:8]}...)"


class APIKeyAuthentication(BaseAuthentication):
    """
    Clients must send:  X-API-Key: <key>

    Valid keys are stored in settings.API_KEYS (list).
    In production, store keys in a database or secrets manager.

    Endpoints decorated with @permission_classes([AllowAny])
    bypass this entirely — used for webhook and health check.
    """

    def authenticate(self, request):
        api_key = request.headers.get(settings.API_KEY_HEADER, "").strip()

        if not api_key:
            return None  # No key provided — let permission class decide

        valid_keys = getattr(settings, "API_KEYS", [])

        if api_key not in valid_keys:
            logger.warning(
                "Invalid API key attempt",
                extra={
                    "ip": _get_client_ip(request),
                    "key_prefix": api_key[:8] if len(api_key) >= 8 else "SHORT",
                    "path": request.path,
                },
            )
            raise AuthenticationFailed("Invalid or missing API key.")

        logger.debug("API key authenticated: %s...", api_key[:8])
        return (APIKeyUser(api_key), api_key)

    def authenticate_header(self, request):
        return settings.API_KEY_HEADER


def _get_client_ip(request) -> str:
    x_forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded:
        return x_forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")