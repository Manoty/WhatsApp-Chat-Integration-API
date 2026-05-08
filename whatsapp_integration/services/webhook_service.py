import logging
from django.utils import timezone

from ..models import BusinessAccount, WhatsAppContact, Conversation, Message
from .media_service import MediaService

from .webhook_dispatcher import WebhookDispatcher
from .event_builder import EventBuilder

from .assignment_engine import AssignmentEngine

logger = logging.getLogger(__name__)


class WebhookService:
    """
    Handles full lifecycle of incoming WhatsApp webhook events.
    """

    # ─────────────────────────────────────────────────────────────
    # ENTRY POINT
    # ─────────────────────────────────────────────────────────────
    def process_incoming_message(self, payload: dict, source: str = "twilio") -> Message | None:
        try:
            if source == "twilio":
                normalized = self._normalize_twilio(payload)
            else:
                normalized = self._normalize_meta(payload)

            if not normalized:
                logger.warning("Unprocessable webhook payload: %s", payload)
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
            if not message:
                return None

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

        # ✅ FIX 1 & 2 — Support multiple media + infer type
        message_type = Message.MessageType.TEXT

        for i in range(num_media):
            url = payload.get(f"MediaUrl{i}")
            content_type = payload.get(f"MediaContentType{i}", "")
            if url:
                media_items.append({
                    "url": url,
                    "content_type": content_type,
                })
                message_type = self._media_type_to_enum(content_type)

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

            # ✅ FIX 3 — Normalize Meta media format
            if message_type_raw == "text":
                body = message_data.get("text", {}).get("body", "")
                message_type = Message.MessageType.TEXT

            else:
                media_blob = message_data.get(message_type_raw, {})
                media_items.append({
                    "url": media_blob.get("id"),  # MediaService will resolve this
                    "content_type": media_blob.get("mime_type", ""),
                })
                message_type = self._media_type_to_enum(media_blob.get("mime_type", ""))

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
    def _get_or_create_conversation(
        self, business, contact
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
            logger.info(
                "New conversation started: %s for contact: %s",
                conversation.id, contact.phone_number,
            )

            # ── Auto-assign to next available agent ───────────────────────────
            try:
                engine = AssignmentEngine()
                agent  = engine.auto_assign(conversation)
                if agent:
                    logger.info(
                        "Conversation auto-assigned | conv=%s | agent=%s",
                        conversation.id, agent.email,
                    )
            except Exception as exc:
                logger.warning(
                    "Auto-assignment failed (non-fatal): %s", exc
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

        # ✅ FIX 4 — Race-safe deduplication
        message, created = Message.objects.get_or_create(
            provider_message_id=provider_id,
            defaults={
                "conversation": conversation,
                "direction": Message.Direction.INBOUND,
                "message_type": normalized["message_type"],
                "body": normalized.get("body", ""),
                "status": Message.Status.DELIVERED,
                "raw_payload": raw_payload,
            },
        )

        if not created:
            logger.info("Duplicate message ignored: %s", provider_id)
            return None

        media_items = normalized.get("media_items", [])
        if media_items:
            media_svc = MediaService()
            for media in media_items:
                try:
                    media_svc.create_attachment(message, media)
                except Exception as exc:
                    logger.warning("Media attachment failed: %s", exc)

        conversation.update_last_message_time()

        try:
            builder = EventBuilder()
            dispatcher = WebhookDispatcher()
            payload = builder.message_received(message)
            dispatcher.dispatch(
                business_id=str(conversation.business_id),
                event_type="message.received",
                payload=payload,
            )
        except Exception as exc:
            logger.warning(
                "Outbound webhook dispatch failed (non-fatal): %s", exc
            )

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