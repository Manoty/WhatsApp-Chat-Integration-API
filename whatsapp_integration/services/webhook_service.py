import logging
from django.utils import timezone
from ..models import BusinessAccount, WhatsAppContact, Conversation, Message
from .media_service import MediaService

logger = logging.getLogger(__name__)


class WebhookService:
    """
    Handles the full lifecycle of an incoming WhatsApp webhook event:
    """

    def process_incoming_message(self, payload: dict, source: str = "twilio") -> Message | None:
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
                return None

            # ── Queue auto-reply task (NON-BLOCKING) ──────────────────────────
            try:
                from ..tasks import process_auto_reply_task

                process_auto_reply_task.apply_async(
                    kwargs={"message_id": str(message.id)},
                    queue="messages",
                    countdown=1,
                )

                logger.info(
                    "Auto-reply task queued | message_id=%s", message.id
                )

            except Exception as exc:
                logger.exception(
                    "Failed to queue auto-reply task (non-fatal): %s", exc
                )

            return message

        except Exception as exc:
            logger.exception("Unexpected error processing webhook: %s", exc)
            return None

    # ── Normalizers ───────────────────────────────────────────────────────────

    def _normalize_twilio(self, payload: dict) -> dict | None:
        from_number = payload.get("From", "").replace("whatsapp:", "").strip()
        to_number = payload.get("To", "").replace("whatsapp:", "").strip()
        body = payload.get("Body", "").strip()
        provider_message_id = payload.get("MessageSid", "")

        if not from_number or not to_number:
            return None

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
        return BusinessAccount.objects.filter(
            phone_number_id=phone_number_id,
            is_active=True,
        ).first()

    # ── Contact Management ────────────────────────────────────────────────────

    def _get_or_create_contact(
        self, business: BusinessAccount, normalized: dict
    ) -> WhatsAppContact:

        contact, created = WhatsAppContact.objects.get_or_create(
            business=business,
            phone_number=normalized["from_number"],
            defaults={
                "display_name": normalized.get("display_name", ""),
            },
        )

        contact.last_seen = timezone.now()

        if normalized.get("display_name") and not contact.display_name:
            contact.display_name = normalized["display_name"]

        contact.save(update_fields=["last_seen", "display_name", "updated_at"])

        if created:
            logger.info(
                "New contact created: %s for business: %s",
                contact.phone_number,
                business.name,
            )

        return contact

    # ── Conversation ─────────────────────────────────────────────────────────

    def _get_or_create_conversation(
        self, business: BusinessAccount, contact: WhatsAppContact
    ) -> Conversation:

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

    # ── Message Storage ───────────────────────────────────────────────────────

    def _store_message(
        self, conversation: Conversation, normalized: dict, raw_payload: dict
    ) -> Message | None:
        """
        Deduplicate by provider_message_id, store the message,
        and create MediaAttachment if media is present.
        """
        provider_id = normalized.get("provider_message_id", "")

        # Deduplication guard
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

        # ── Handle media attachments ──────────────────────────────────────────
        media_items = normalized.get("media_items", [])
        if media_items:
            media_svc = MediaService()
            for media_dict in media_items:
                try:
                    media_svc.create_attachment(message, media_dict)
                except Exception as exc:
                    logger.warning(
                        "Failed to create media attachment: %s", exc
                    )

        conversation.update_last_message_time()

        logger.info(
            "Message stored | id=%s | type=%s | media_count=%d | from=%s",
            message.id,
            normalized["message_type"],
            len(media_items),
            normalized["from_number"],
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