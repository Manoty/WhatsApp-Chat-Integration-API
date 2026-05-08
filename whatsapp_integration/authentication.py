import logging
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied

logger = logging.getLogger(__name__)


def _get_client_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


class APIKeyUser:
    """
    Lightweight user object returned by authentication.
    Carries the APIKey model instance for scope + IP checks downstream.
    """
    is_authenticated = True

    def __init__(self, api_key):
        self.api_key    = api_key
        self.pk         = str(api_key.id)
        self.business   = api_key.business
        self.scope      = api_key.scope

    def __str__(self):
        return f"APIKey({self.api_key.prefix}... | {self.scope})"


class APIKeyAuthentication(BaseAuthentication):
    """
    Database-backed API key authentication.

    Clients send:  X-API-Key: sk_live_<key>

    On each request:
      1. Extract raw key from header
      2. Hash it → look up in DB
      3. Check status (active, not expired)
      4. Check IP allowlist
      5. Check scope vs HTTP method
      6. Update last_used_at + request_count
    """

    HEADER = "X-API-Key"

    def authenticate(self, request):
        from django.conf import settings

        # ── Support legacy env-var keys during migration ──────────────────────
        raw_key = request.headers.get(self.HEADER, "").strip()
        if not raw_key:
            return None

        # Check legacy keys first (for backwards compatibility)
        legacy_keys = getattr(settings, "API_KEYS", [])
        if raw_key in legacy_keys:
            logger.debug("Legacy API key used — migrate to DB keys")
            return (self._make_legacy_user(raw_key), raw_key)

        # ── DB-backed key lookup ──────────────────────────────────────────────
        from .models import APIKey

        api_key = APIKey.authenticate(raw_key)

        if not api_key:
            logger.warning(
                "Invalid API key attempt",
                extra={
                    "ip":         _get_client_ip(request),
                    "key_prefix": raw_key[:12],
                    "path":       request.path,
                },
            )
            raise AuthenticationFailed("Invalid or expired API key.")

        # ── IP allowlist check ────────────────────────────────────────────────
        client_ip = _get_client_ip(request)
        if not api_key.allows_ip(client_ip):
            logger.warning(
                "API key IP denied",
                extra={
                    "ip":         client_ip,
                    "key_prefix": api_key.prefix,
                    "allowed":    api_key.allowed_ips,
                },
            )
            raise AuthenticationFailed(
                f"IP {client_ip} is not in this key's allowlist."
            )

        # ── Scope check ───────────────────────────────────────────────────────
        if not api_key.allows_method(request.method):
            logger.warning(
                "API key scope denied",
                extra={
                    "scope":  api_key.scope,
                    "method": request.method,
                    "path":   request.path,
                },
            )
            raise PermissionDenied(
                f"This key has '{api_key.scope}' scope and cannot "
                f"perform {request.method} requests."
            )

        logger.debug(
            "API key authenticated | prefix=%s | scope=%s | business=%s",
            api_key.prefix, api_key.scope, api_key.business.name,
        )

        return (APIKeyUser(api_key), raw_key)

    def authenticate_header(self, request):
        return self.HEADER

    def _make_legacy_user(self, raw_key: str):
        """Wrap a legacy env-var key in a compatible user object."""

        class LegacyUser:
            is_authenticated = True
            pk               = raw_key
            scope            = "admin"

            def __str__(self):
                return f"LegacyKey({raw_key[:8]}...)"

        return LegacyUser()