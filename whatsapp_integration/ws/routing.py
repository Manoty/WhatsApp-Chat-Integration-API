from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    # Business dashboard feed
    # ws://localhost:8000/ws/business/<business_id>/
    re_path(
        r"^ws/business/(?P<business_id>[0-9a-f-]+)/$",
        consumers.BusinessConsumer.as_asgi(),
        name="ws-business",
    ),

    # Agent-specific feed
    # ws://localhost:8000/ws/agent/<agent_id>/
    re_path(
        r"^ws/agent/(?P<agent_id>[0-9a-f-]+)/$",
        consumers.AgentConsumer.as_asgi(),
        name="ws-agent",
    ),

    # Conversation-specific feed
    # ws://localhost:8000/ws/conversation/<conversation_id>/
    re_path(
        r"^ws/conversation/(?P<conversation_id>[0-9a-f-]+)/$",
        consumers.ConversationConsumer.as_asgi(),
        name="ws-conversation",
    ),
]