import logging
from django.utils import timezone
from ..models import BusinessAccount, WhatsAppContact, Conversation, Message

from .auto_reply_engine import AutoReplyEngine

logger = logging.getLogger(__name__)


class WebhookService:
    """
    Handles the full lifecycle of an incoming WhatsApp webhook event:
      1. Parse and normalize the payload (Twilio or Meta format)
      2. Resolve the BusinessAccount
      3. Get or create the WhatsAppContact
      4. Get or create the Conversation
      5. Deduplicate and store the Message
    """

    # ── Public entry point ────────────────────────────────────────────────────

    def process_incoming_message(self, payload: dict, source: str = "twilio") -> Message | None:
    """
    Main method called by the webhook view.
    Returns the saved Message object or None if skipped (duplicate).
    Now triggers AutoReplyEngine after storing every inbound message.
    """
    try:
        if source == "twilio":
            normalized = self._normalize_twilio(payload)
        else:
            normalized = self._normalize_meta(payload)

        if not normalized:
            logger.warning("Webhook payload could not be normalized: %s", payload)
            return None

        business = self._resolve_business(normalized["to_number"])
        if not business:
            logger.warning(
                "No BusinessAccount found for phone_number_id=%s",
                normalized["to_number"],
            )
            return None

        contact = self._get_or_create_contact(business, normalized)
        conversation = self._get_or_create_conversation(business, contact)
        message = self._store_message(conversation, normalized, payload)

        if message is None:
            return None  # Duplicate — already handled

        # ── Trigger auto-reply engine ─────────────────────────────────────────
        try:
            engine = AutoReplyEngine()
            engine.process(message)
        except Exception as exc:
            # Never let auto-reply crash the webhook response
            logger.exception("AutoReplyEngine error (non-fatal): %s", exc)

        return message

    except Exception as exc:
        logger.exception("Unexpected error processing webhook: %s", exc)
        return None

    # ── Normalizers ───────────────────────────────────────────────────────────

    def _normalize_twilio(self, payload: dict) -> dict | None:
        """
        Twilio WhatsApp webhook payload shape:
        {
            "MessageSid": "SMxxx",
            "From": "whatsapp:+254712345678",
            "To": "whatsapp:+254700000000",
            "Body": "Hello there",
            "NumMedia": "0",
            ...
        }
        """
        from_number = payload.get("From", "").replace("whatsapp:", "").strip()
        to_number = payload.get("To", "").replace("whatsapp:", "").strip()
        body = payload.get("Body", "").strip()
        provider_message_id = payload.get("MessageSid", "")

        if not from_number or not to_number:
            return None

        # Detect media type
        num_media = int(payload.get("NumMedia", 0))
        if num_media > 0:
            media_type = payload.get("MediaContentType0", "")
            message_type = self._media_type_to_enum(media_type)
        else:
            message_type = Message.MessageType.TEXT

        return {
            "from_number": from_number,
            "to_number": to_number,
            "body": body,
            "provider_message_id": provider_message_id,
            "message_type": message_type,
            "display_name": payload.get("ProfileName", ""),
        }

    def _normalize_meta(self, payload: dict) -> dict | None:
        """
        Meta (WhatsApp Business API) webhook payload shape:
        {
          "object": "whatsapp_business_account",
          "entry": [{
            "changes": [{
              "value": {
                "metadata": {"phone_number_id": "..."},
                "contacts": [{"profile": {"name": "..."}, "wa_id": "..."}],
                "messages": [{"id": "...", "from": "...", "text": {"body": "..."}, "type": "text"}]
              }
            }]
          }]
        }
        """
        try:
            value = payload["entry"][0]["changes"][0]["value"]
            message_data = value["messages"][0]
            contact_data = value["contacts"][0]
            metadata = value["metadata"]

            from_number = f"+{message_data['from']}"
            to_number = metadata["phone_number_id"]
            message_type_raw = message_data.get("type", "text")
            body = ""

            if message_type_raw == "text":
                body = message_data.get("text", {}).get("body", "")
                message_type = Message.MessageType.TEXT
            elif message_type_raw == "image":
                message_type = Message.MessageType.IMAGE
            elif message_type_raw == "audio":
                message_type = Message.MessageType.AUDIO
            elif message_type_raw == "video":
                message_type = Message.MessageType.VIDEO
            elif message_type_raw == "document":
                message_type = Message.MessageType.DOCUMENT
            else:
                message_type = Message.MessageType.TEXT

            return {
                "from_number": from_number,
                "to_number": to_number,
                "body": body,
                "provider_message_id": message_data["id"],
                "message_type": message_type,
                "display_name": contact_data.get("profile", {}).get("name", ""),
            }

        except (KeyError, IndexError) as exc:
            logger.warning("Could not parse Meta payload: %s | Error: %s", payload, exc)
            return None

    # ── Business Resolution ───────────────────────────────────────────────────

    def _resolve_business(self, phone_number_id: str) -> BusinessAccount | None:
        """Find the BusinessAccount that owns this WhatsApp number."""
        return BusinessAccount.objects.filter(
            phone_number_id=phone_number_id,
            is_active=True,
        ).first()

    # ── Contact Management ────────────────────────────────────────────────────

    def _get_or_create_contact(
        self, business: BusinessAccount, normalized: dict
    ) -> WhatsAppContact:
        """
        Find existing contact or create a new one.
        Updates display_name and last_seen on every inbound message.
        """
        contact, created = WhatsAppContact.objects.get_or_create(
            business=business,
            phone_number=normalized["from_number"],
            defaults={
                "display_name": normalized.get("display_name", ""),
            },
        )

        # Always refresh last_seen and name
        contact.last_seen = timezone.now()
        if normalized.get("display_name") and not contact.display_name:
            contact.display_name = normalized["display_name"]
        contact.save(update_fields=["last_seen", "display_name", "updated_at"])

        if created:
            logger.info("New contact created: %s for business: %s", contact.phone_number, business.name)

        return contact

    # ── Conversation Management ───────────────────────────────────────────────

    def _get_or_create_conversation(
        self, business: BusinessAccount, contact: WhatsAppContact
    ) -> Conversation:
        """
        Get the active (open) conversation or create a fresh one.
        One open conversation per contact per business at a time.
        """
        conversation = Conversation.objects.filter(
            business=business,
            contact=contact,
            status=Conversation.Status.OPEN,
        ).first()

        if not conversation:
            conversation = Conversation.objects.create(
                business=business,
                contact=contact,
                status=Conversation.Status.OPEN,
            )
            logger.info(
                "New conversation started: %s for contact: %s",
                conversation.id,
                contact.phone_number,
            )

        return conversation

    # ── Message Storage ───────────────────────────────────────────────────────

    def _store_message(
        self, conversation: Conversation, normalized: dict, raw_payload: dict
    ) -> Message | None:
        """
        Deduplicate by provider_message_id, then store the message.
        """
        provider_id = normalized.get("provider_message_id", "")

        # Deduplication guard — WhatsApp retries on timeout
        if provider_id and Message.objects.filter(provider_message_id=provider_id).exists():
            logger.info("Duplicate message ignored: %s", provider_id)
            return None

        message = Message.objects.create(
            conversation=conversation,
            direction=Message.Direction.INBOUND,
            message_type=normalized["message_type"],
            body=normalized["body"],
            provider_message_id=provider_id,
            status=Message.Status.DELIVERED,  # inbound = already delivered to us
            raw_payload=raw_payload,
        )

        # Keep conversation's last_message_at fresh
        conversation.update_last_message_time()

        logger.info(
            "Message stored: id=%s | from=%s | body='%s'",
            message.id,
            normalized["from_number"],
            normalized["body"][:60],
        )

        return message

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _media_type_to_enum(self, content_type: str) -> str:
        mapping = {
            "image": Message.MessageType.IMAGE,
            "audio": Message.MessageType.AUDIO,
            "video": Message.MessageType.VIDEO,
            "application": Message.MessageType.DOCUMENT,
        }
        prefix = content_type.split("/")[0] if content_type else ""
        return mapping.get(prefix, Message.MessageType.TEXT)