import uuid
from django.utils import timezone
from ..models import Message, Conversation, WhatsAppContact


class EventBuilder:
    """
    Builds clean, versioned event payloads for outbound webhooks.

    Every payload follows this envelope:
    {
        "id":          "<unique event id>",
        "event":       "message.received",
        "version":     "1.0",
        "timestamp":   "2026-05-05T10:00:00Z",
        "business_id": "<uuid>",
        "data":        { ... event-specific data ... }
    }
    """

    VERSION = "1.0"

    def build(self, event_type: str, business_id: str, data: dict) -> dict:
        """Base envelope — all events share this structure."""
        return {
            "id":          str(uuid.uuid4()),
            "event":       event_type,
            "version":     self.VERSION,
            "timestamp":   timezone.now().isoformat(),
            "business_id": business_id,
            "data":        data,
        }

    # ── Message Events ────────────────────────────────────────────────────────

    def message_received(self, message: Message) -> dict:
        return self.build(
            event_type="message.received",
            business_id=str(message.conversation.business_id),
            data={
                "message_id":       str(message.id),
                "conversation_id":  str(message.conversation_id),
                "direction":        message.direction,
                "message_type":     message.message_type,
                "body":             message.body,
                "status":           message.status,
                "from_number":      message.conversation.contact.phone_number,
                "contact_name":     message.conversation.contact.display_name,
                "provider_message_id": message.provider_message_id,
                "has_media":        hasattr(message, "media_attachment"),
                "received_at":      message.created_at.isoformat(),
            },
        )

    def message_sent(self, message: Message) -> dict:
        return self.build(
            event_type="message.sent",
            business_id=str(message.conversation.business_id),
            data={
                "message_id":          str(message.id),
                "conversation_id":     str(message.conversation_id),
                "direction":           message.direction,
                "message_type":        message.message_type,
                "body":                message.body,
                "to_number":           message.conversation.contact.phone_number,
                "provider_message_id": message.provider_message_id,
                "sent_at":             message.created_at.isoformat(),
            },
        )

    def message_status_changed(
        self, message: Message, event_type: str
    ) -> dict:
        return self.build(
            event_type=event_type,
            business_id=str(message.conversation.business_id),
            data={
                "message_id":          str(message.id),
                "conversation_id":     str(message.conversation_id),
                "provider_message_id": message.provider_message_id,
                "status":              message.status,
                "to_number":           message.conversation.contact.phone_number,
                "updated_at":          message.status_updated_at.isoformat()
                                       if message.status_updated_at else None,
            },
        )

    # ── Conversation Events ───────────────────────────────────────────────────

    def conversation_opened(self, conversation: Conversation) -> dict:
        return self.build(
            event_type="conversation.opened",
            business_id=str(conversation.business_id),
            data={
                "conversation_id": str(conversation.id),
                "status":          conversation.status,
                "contact_phone":   conversation.contact.phone_number,
                "contact_name":    conversation.contact.display_name,
                "opened_at":       conversation.created_at.isoformat(),
            },
        )

    def conversation_closed(self, conversation: Conversation) -> dict:
        return self.build(
            event_type="conversation.closed",
            business_id=str(conversation.business_id),
            data={
                "conversation_id": str(conversation.id),
                "contact_phone":   conversation.contact.phone_number,
                "contact_name":    conversation.contact.display_name,
                "closed_at":       timezone.now().isoformat(),
                "message_count":   conversation.messages.count(),
            },
        )

    # ── Contact Events ────────────────────────────────────────────────────────

    def contact_created(self, contact: WhatsAppContact) -> dict:
        return self.build(
            event_type="contact.created",
            business_id=str(contact.business_id),
            data={
                "contact_id":   str(contact.id),
                "phone_number": contact.phone_number,
                "display_name": contact.display_name,
                "created_at":   contact.created_at.isoformat(),
            },
        )