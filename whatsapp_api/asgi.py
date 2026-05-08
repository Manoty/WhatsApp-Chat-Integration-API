import os
import django
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "whatsapp_api.settings")
django.setup()

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from whatsapp_integration.ws.middleware import JWTWebSocketMiddleware
from whatsapp_integration.ws.routing import websocket_urlpatterns

application = ProtocolTypeRouter({
    # Standard HTTP → Django views (unchanged)
    "http": get_asgi_application(),

    # WebSocket → Channels consumers
    "websocket": AllowedHostsOriginValidator(
        JWTWebSocketMiddleware(
            URLRouter(websocket_urlpatterns)
        )
    ),
})