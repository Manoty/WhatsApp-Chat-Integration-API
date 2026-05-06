import logging
from django.utils import timezone

from ..models import BusinessAccount, WhatsAppContact, Conversation, Message
from .media_service import MediaService

logger = logging.getLogger(__name__)


class WebhookService:
    """
    Handles full lifecycle of incoming WhatsApp webhook events:
    - Normalize payload (Twilio / Meta)
    - Resolve business
    - Manage contact + conversation
    - Store message
    - Queue async auto-reply
    """

    # ─────────────────────────────────────────────────────────────
    # ENTRY POINT
    # ─────────────────────────────────────────────────────────────
    def process_incoming_message(self, payload: dict, source: str = "twilio") -> Message | None:
        try:
            # ── Normalize payload ───────────────────────────────
            if source == "twilio":
                normalized = self._normalize_twilio(payload)
            else:
                normalized = self._normalize_meta(payload)

            if not normalized:
                logger.warning("Unprocessable webhook payload: %s", payload)
                return None

            # ── Resolve business ────────────────────────────────
            business = self._resolve_business(normalized["to_number"])
            if not business:
                logger.warning(
                    "No BusinessAccount found for phone_number_id=%s",
                    normalized["to_number"],
                )
                return None

            # ── Contact + conversation ─────────────────────────
            contact = self._get_or_create_contact(business, normalized)
            conversation = self._get_or_create_conversation(business, contact)

            # ── Store message ───────────────────────────────────
            message = self._store_message(conversation, normalized, payload)
            if not message:
                return None

            # ── Async auto-reply (Celery) ───────────────────────
            try:
                from ..tasks import process_auto_reply_task

                process_auto_reply_task.apply_async(
                    kwargs={"message_id": str(message.id)},
                    queue="messages",
                    countdown=1,
                )

                logger.info("Auto-reply queued | message_id=%s", message.id)

            except Exception as exc:
                logger.exception("Failed to queue auto-reply task: %s", exc)

            return message

        except Exception as exc:
            logger.exception("Webhook processing failed: %s", exc)
            return None

    # ─────────────────────────────────────────────────────────────
    # NORMALIZERS
    # ─────────────────────────────────────────────────────────────
    def _normalize_twilio(self, payload: dict) -> dict | None:
        from_number = payload.get("From", "").replace("whatsapp:", "").strip()
        to_number = payload.get("To", "").replace("whatsapp:", "").strip()
        body = payload.get("Body", "").strip()
        provider_message_id = payload.get("MessageSid", "")

        if not from_number or not to_number:
            return None

        num_media = int(payload.get("NumMedia", 0))

        media_items = []
        if num_media > 0:
            media_items.append({
                "url": payload.get("MediaUrl0"),
                "content_type": payload.get("MediaContentType0"),
            })

        message_type = (
            Message.MessageType.IMAGE
            if num_media > 0
            else Message.MessageType.TEXT
        )

        return {
            "from_number": from_number,
            "to_number": to_number,
            "body": body,
            "provider_message_id": provider_message_id,
            "message_type": message_type,
            "display_name": payload.get("ProfileName", ""),
            "media_items": media_items,
        }

    def _normalize_meta(self, payload: dict) -> dict | None:
        try:
            value = payload["entry"][0]["changes"][0]["value"]
            message_data = value["messages"][0]
            contact_data = value["contacts"][0]
            metadata = value["metadata"]

            from_number = f"+{message_data['from']}"
            to_number = metadata["phone_number_id"]
            message_type_raw = message_data.get("type", "text")

            body = ""
            media_items = []

            if message_type_raw == "text":
                body = message_data.get("text", {}).get("body", "")
                message_type = Message.MessageType.TEXT

            elif message_type_raw == "image":
                message_type = Message.MessageType.IMAGE
                media_items.append(message_data.get("image", {}))

            elif message_type_raw == "audio":
                message_type = Message.MessageType.AUDIO
                media_items.append(message_data.get("audio", {}))

            elif message_type_raw == "video":
                message_type = Message.MessageType.VIDEO
                media_items.append(message_data.get("video", {}))

            elif message_type_raw == "document":
                message_type = Message.MessageType.DOCUMENT
                media_items.append(message_data.get("document", {}))

            else:
                message_type = Message.MessageType.TEXT

            return {
                "from_number": from_number,
                "to_number": to_number,
                "body": body,
                "provider_message_id": message_data["id"],
                "message_type": message_type,
                "display_name": contact_data.get("profile", {}).get("name", ""),
                "media_items": media_items,
            }

        except (KeyError, IndexError) as exc:
            logger.warning("Meta payload parse error: %s | %s", payload, exc)
            return None

    # ─────────────────────────────────────────────────────────────
    # BUSINESS RESOLUTION
    # ─────────────────────────────────────────────────────────────
    def _resolve_business(self, phone_number_id: str) -> BusinessAccount | None:
        return BusinessAccount.objects.filter(
            phone_number_id=phone_number_id,
            is_active=True,
        ).first()

    # ─────────────────────────────────────────────────────────────
    # CONTACT
    # ─────────────────────────────────────────────────────────────
    def _get_or_create_contact(self, business, normalized: dict) -> WhatsAppContact:
        contact, created = WhatsAppContact.objects.get_or_create(
            business=business,
            phone_number=normalized["from_number"],
            defaults={"display_name": normalized.get("display_name", "")},
        )

        contact.last_seen = timezone.now()

        if normalized.get("display_name") and not contact.display_name:
            contact.display_name = normalized["display_name"]

        contact.save(update_fields=["last_seen", "display_name", "updated_at"])

        if created:
            logger.info("New contact created: %s", contact.phone_number)

        return contact

    # ─────────────────────────────────────────────────────────────
    # CONVERSATION
    # ─────────────────────────────────────────────────────────────
    def _get_or_create_conversation(self, business, contact) -> Conversation:
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

        return conversation

    # ─────────────────────────────────────────────────────────────
    # MESSAGE STORAGE
    # ─────────────────────────────────────────────────────────────
    def _store_message(
        self,
        conversation: Conversation,
        normalized: dict,
        raw_payload: dict,
    ) -> Message | None:

        provider_id = normalized.get("provider_message_id", "")

        # Deduplication
        if provider_id and Message.objects.filter(
            provider_message_id=provider_id
        ).exists():
            logger.info("Duplicate message ignored: %s", provider_id)
            return None

        message = Message.objects.create(
            conversation=conversation,
            direction=Message.Direction.INBOUND,
            message_type=normalized["message_type"],
            body=normalized.get("body", ""),
            provider_message_id=provider_id,
            status=Message.Status.DELIVERED,
            raw_payload=raw_payload,
        )

        # ── Media attachments ───────────────────────────────
        media_items = normalized.get("media_items", [])

        if media_items:
            media_svc = MediaService()
            for media in media_items:
                try:
                    media_svc.create_attachment(message, media)
                except Exception as exc:
                    logger.warning("Media attachment failed: %s", exc)

        conversation.update_last_message_time()

        logger.info(
            "Message stored | id=%s | type=%s | media=%d | from=%s",
            message.id,
            normalized["message_type"],
            len(media_items),
            normalized["from_number"],
        )

        return message

    # ─────────────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────────────
    def _media_type_to_enum(self, content_type: str) -> str:
        mapping = {
            "image": Message.MessageType.IMAGE,
            "audio": Message.MessageType.AUDIO,
            "video": Message.MessageType.VIDEO,
            "application": Message.MessageType.DOCUMENT,
        }

        prefix = content_type.split("/")[0] if content_type else ""
        return mapping.get(prefix, Message.MessageType.TEXT)