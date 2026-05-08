import logging
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Message, Conversation, Agent, WhatsAppContact

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Message)
def on_message_saved(sender, instance: Message, created: bool, **kwargs):
    """
    Push WebSocket event when a message is created or its status changes.
    """
    try:
        from .ws.channel_utils import push_to_business, push_to_conversation

        conv        = instance.conversation
        business_id = str(conv.business_id)
        conv_id     = str(conv.id)

        if created:
            event_type = (
                "message.received"
                if instance.direction == "inbound"
                else "message.sent"
            )
        else:
            status_event_map = {
                "delivered": "message.delivered",
                "read":      "message.read",
                "failed":    "message.failed",
                "sent":      "message.sent",
            }
            event_type = status_event_map.get(
                instance.status, "message.status_changed"
            )

        payload = {
            "message_id":      str(instance.id),
            "conversation_id": conv_id,
            "direction":       instance.direction,
            "message_type":    instance.message_type,
            "body":            instance.body,
            "status":          instance.status,
            "created_at":      instance.created_at.isoformat(),
        }

        push_to_business(business_id, event_type, payload)
        push_to_conversation(conv_id, event_type, payload)

    except Exception as exc:
        logger.warning("Signal WS push failed (non-fatal): %s", exc)


@receiver(post_save, sender=Conversation)
def on_conversation_saved(
    sender, instance: Conversation, created: bool, **kwargs
):
    """Push WebSocket event when a conversation is created or updated."""
    try:
        from .ws.channel_utils import push_to_business

        business_id = str(instance.business_id)

        if created:
            event_type = "conversation.opened"
        elif instance.status == "closed":
            event_type = "conversation.closed"
        else:
            event_type = "conversation.updated"

        payload = {
            "conversation_id": str(instance.id),
            "status":          instance.status,
            "assigned_to":     instance.assigned_to,
            "last_message_at": (
                instance.last_message_at.isoformat()
                if instance.last_message_at else None
            ),
        }

        push_to_business(business_id, event_type, payload)

    except Exception as exc:
        logger.warning(
            "Conversation signal WS push failed (non-fatal): %s", exc
        )


@receiver(post_save, sender=Agent)
def on_agent_saved(sender, instance: Agent, created: bool, **kwargs):
    """Push WebSocket event when an agent status changes."""
    try:
        from .ws.channel_utils import push_to_business, push_to_agent

        business_id = str(instance.business_id)
        agent_id    = str(instance.id)

        event_type = "agent.created" if created else "agent.status_changed"
        payload = {
            "agent_id":             agent_id,
            "name":                 instance.name,
            "email":                instance.email,
            "status":               instance.status,
            "active_conversations": instance.active_conversation_count,
        }

        push_to_business(business_id, event_type, payload)
        push_to_agent(agent_id, event_type, payload)

    except Exception as exc:
        logger.warning(
            "Agent signal WS push failed (non-fatal): %s", exc
        )


@receiver(post_save, sender=WhatsAppContact)
def on_contact_saved(
    sender, instance: WhatsAppContact, created: bool, **kwargs
):
    """Push WebSocket event when a new contact is created."""
    if not created:
        return

    try:
        from .ws.channel_utils import push_to_business

        push_to_business(
            str(instance.business_id),
            "contact.created",
            {
                "contact_id":   str(instance.id),
                "phone_number": instance.phone_number,
                "display_name": instance.display_name,
            },
        )
    except Exception as exc:
        logger.warning(
            "Contact signal WS push failed (non-fatal): %s", exc
        )