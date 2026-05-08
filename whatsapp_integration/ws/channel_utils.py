import json
import logging
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


def business_group_name(business_id: str) -> str:
    """Channel group name for a business dashboard."""
    return f"business_{str(business_id).replace('-', '_')}"


def agent_group_name(agent_id: str) -> str:
    """Channel group name for an agent feed."""
    return f"agent_{str(agent_id).replace('-', '_')}"


def conversation_group_name(conversation_id: str) -> str:
    """Channel group name for a conversation thread."""
    return f"conv_{str(conversation_id).replace('-', '_')}"


def push_to_business(business_id: str, event_type: str, data: dict):
    """
    Push an event to all WebSocket clients watching a business.
    Safe to call from synchronous Django code (views, services, tasks).
    """
    _push(business_group_name(business_id), event_type, data)


def push_to_agent(agent_id: str, event_type: str, data: dict):
    """Push an event to a specific agent's WebSocket feed."""
    _push(agent_group_name(agent_id), event_type, data)


def push_to_conversation(conversation_id: str, event_type: str, data: dict):
    """Push an event to all clients watching a conversation."""
    _push(conversation_group_name(conversation_id), event_type, data)


def _push(group_name: str, event_type: str, data: dict):
    """
    Internal: send a message to a channel group.
    Uses async_to_sync so it works from sync Django code.
    """
    try:
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                "type":       "ws.event",    # maps to ws_event() in consumer
                "event_type": event_type,
                "data":       data,
            },
        )
        logger.debug(
            "WS push | group=%s | event=%s", group_name, event_type
        )
    except Exception as exc:
        # Never crash the caller — WebSocket push is best-effort
        logger.warning(
            "WS push failed (non-fatal) | group=%s | event=%s | error=%s",
            group_name, event_type, exc,
        )