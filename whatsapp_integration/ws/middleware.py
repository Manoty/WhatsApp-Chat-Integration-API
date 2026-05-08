import logging
from urllib.parse import parse_qs
from channels.middleware import BaseMiddleware
from channels.db import database_sync_to_async

logger = logging.getLogger(__name__)


class JWTWebSocketMiddleware(BaseMiddleware):
    """
    Authenticates WebSocket connections via API key.

    Clients connect with:
      ws://localhost:8000/ws/business/<id>/?api_key=sk_live_xxx
      OR
      ws://localhost:8000/ws/business/<id>/?legacy_key=dev-key-12345

    On success: sets scope["api_key"] and scope["business"]
    On failure: closes connection with code 4001
    """

    async def __call__(self, scope, receive, send):
        scope["api_key"]  = None
        scope["business"] = None
        scope["agent"]    = None

        query_string = scope.get("query_string", b"").decode()
        params       = parse_qs(query_string)

        raw_key      = params.get("api_key",    [None])[0]
        legacy_key   = params.get("legacy_key", [None])[0]

        authenticated = False

        if raw_key:
            authenticated = await self._authenticate_db_key(scope, raw_key)
        elif legacy_key:
            authenticated = await self._authenticate_legacy_key(scope, legacy_key)

        if not authenticated:
            logger.warning(
                "WebSocket auth failed | path=%s", scope.get("path")
            )
            # Close with policy violation code
            await send({
                "type":  "websocket.close",
                "code":  4001,
            })
            return

        await super().__call__(scope, receive, send)

    @database_sync_to_async
    def _authenticate_db_key(self, scope: dict, raw_key: str) -> bool:
        from whatsapp_integration.models import APIKey
        api_key = APIKey.authenticate(raw_key)
        if not api_key:
            return False
        scope["api_key"]  = api_key
        scope["business"] = api_key.business
        return True

    @database_sync_to_async
    def _authenticate_legacy_key(self, scope: dict, legacy_key: str) -> bool:
        from django.conf import settings
        valid_keys = getattr(settings, "API_KEYS", [])
        if legacy_key not in valid_keys:
            return False
        scope["api_key"]  = legacy_key
        scope["business"] = None   # legacy keys aren't scoped to a business
        return True