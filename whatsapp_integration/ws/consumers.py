import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .channel_utils import (
    business_group_name,
    agent_group_name,
    conversation_group_name,
)

logger = logging.getLogger(__name__)


class BaseConsumer(AsyncWebsocketConsumer):
    """
    Shared base for all consumers.
    Handles connect/disconnect/error + standardised event dispatch.
    """

    group_name: str = ""

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self):
        if not self.scope.get("api_key"):
            logger.warning("Unauthenticated WS connection rejected")
            await self.close(code=4001)
            return

        await self.channel_layer.group_add(
            self.group_name, self.channel_name
        )
        await self.accept()

        # Send connection confirmation
        await self.send_event("connection.established", {
            "group":   self.group_name,
            "channel": self.channel_name,
            "message": "Connected to WhatsApp API live feed",
        })

        logger.info(
            "WS connected | group=%s | channel=%s",
            self.group_name, self.channel_name,
        )

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            self.group_name, self.channel_name
        )
        logger.info(
            "WS disconnected | group=%s | code=%s",
            self.group_name, close_code,
        )

    async def receive(self, text_data=None, bytes_data=None):
        """
        Handle messages sent FROM the client to the server.
        Currently supports: ping, typing indicators.
        """
        if not text_data:
            return

        try:
            data      = json.loads(text_data)
            action    = data.get("action", "")
            payload   = data.get("data", {})

            if action == "ping":
                await self.send_event("pong", {"ts": payload.get("ts")})

            elif action == "typing.start":
                await self._handle_typing(payload, is_typing=True)

            elif action == "typing.stop":
                await self._handle_typing(payload, is_typing=False)

        except json.JSONDecodeError:
            await self.send_event("error", {"message": "Invalid JSON"})

    # ── Incoming channel layer events ─────────────────────────────────────────

    async def ws_event(self, event):
        """
        Called when channel_layer.group_send() fires a 'ws.event'.
        The `type` field maps to this method name (dots → underscores).
        """
        await self.send_event(event["event_type"], event["data"])

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def send_event(self, event_type: str, data: dict):
        """Send a structured event to this WebSocket client."""
        from django.utils import timezone
        try:
            await self.send(text_data=json.dumps({
                "event":     event_type,
                "data":      data,
                "timestamp": timezone.now().isoformat(),
            }))
        except Exception as exc:
            logger.warning("WS send failed: %s", exc)

    async def _handle_typing(self, payload: dict, is_typing: bool):
        """Broadcast typing indicator to the group."""
        event_type = "typing.start" if is_typing else "typing.stop"
        await self.channel_layer.group_send(
            self.group_name,
            {
                "type":            "ws.event",
                "event_type":      event_type,
                "data": {
                    "conversation_id": payload.get("conversation_id"),
                    "agent":           payload.get("agent"),
                },
            },
        )


class BusinessConsumer(BaseConsumer):
    """
    Feed for an entire business — receives ALL events for that tenant.
    Used by: supervisor dashboards, CRM integrations, admin panels.

    URL: ws://localhost:8000/ws/business/<business_id>/
    Events: message.*, conversation.*, agent.*, contact.*
    """

    async def connect(self):
        business_id = self.scope["url_route"]["kwargs"]["business_id"]

        # Validate this business exists and the key has access
        valid = await self._validate_business_access(business_id)
        if not valid:
            await self.close(code=4003)
            return

        self.group_name = business_group_name(business_id)
        await super().connect()

    @database_sync_to_async
    def _validate_business_access(self, business_id: str) -> bool:
        from whatsapp_integration.models import BusinessAccount
        try:
            biz = BusinessAccount.objects.get(id=business_id, is_active=True)
            # If key is scoped to a business, validate it matches
            scoped_biz = self.scope.get("business")
            if scoped_biz and str(scoped_biz.id) != business_id:
                return False
            return True
        except BusinessAccount.DoesNotExist:
            return False


class AgentConsumer(BaseConsumer):
    """
    Feed for a specific agent — receives events for their assigned conversations.
    Used by: agent chat interfaces.

    URL: ws://localhost:8000/ws/agent/<agent_id>/
    Events: conversation.assigned, message.received (their convos), typing.*
    """

    async def connect(self):
        agent_id = self.scope["url_route"]["kwargs"]["agent_id"]

        valid = await self._validate_agent(agent_id)
        if not valid:
            await self.close(code=4003)
            return

        self.group_name = agent_group_name(agent_id)
        await super().connect()

    @database_sync_to_async
    def _validate_agent(self, agent_id: str) -> bool:
        from whatsapp_integration.models import Agent
        try:
            Agent.objects.get(id=agent_id)
            return True
        except Agent.DoesNotExist:
            return False


class ConversationConsumer(BaseConsumer):
    """
    Feed for a single conversation thread — receives all events for that thread.
    Used by: chat UIs, conversation detail views.

    URL: ws://localhost:8000/ws/conversation/<conversation_id>/
    Events: message.received, message.sent, typing.*, message.status_changed
    """

    async def connect(self):
        conversation_id = self.scope["url_route"]["kwargs"]["conversation_id"]

        valid = await self._validate_conversation(conversation_id)
        if not valid:
            await self.close(code=4003)
            return

        self.group_name = conversation_group_name(conversation_id)
        await super().connect()

    @database_sync_to_async
    def _validate_conversation(self, conversation_id: str) -> bool:
        from whatsapp_integration.models import Conversation
        try:
            Conversation.objects.get(id=conversation_id)
            return True
        except Conversation.DoesNotExist:
            return False