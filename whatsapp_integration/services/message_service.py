import logging
from django.utils import timezone
from ..models import BusinessAccount, WhatsAppContact, Conversation, Message
from .whatsapp_client import get_whatsapp_client

from .webhook_dispatcher import WebhookDispatcher
from .event_builder import EventBuilder

logger = logging.getLogger(__name__)


class MessageSendError(Exception):
    """Raised when a message cannot be sent."""
    pass


class MessageService:
    """
    Handles sending outbound WhatsApp messages.
    """

    # ── SYNC SEND ────────────────────────────────────────────────────────────
    def send_message(
        self,
        business_id: str,
        to_number: str,
        body: str,
        message_type: str = Message.MessageType.TEXT,
    ) -> Message:

        business = self._get_business(business_id)
        to_number = self._normalize_phone(to_number)

        contact = self._get_or_create_contact(business, to_number)
        conversation = self._get_or_create_conversation(business, contact)

        message = Message.objects.create(
            conversation=conversation,
            direction=Message.Direction.OUTBOUND,
            message_type=message_type,
            body=body,
            status=Message.Status.PENDING,
        )

        client = get_whatsapp_client()
        result = client.send_text_message(
            to_number=to_number,
            body=body,
            from_number=business.phone_number_id,
        )

        if result.success:
            message.status = Message.Status.SENT
            message.provider_message_id = result.provider_message_id
            message.raw_payload = result.raw_response
        else:
            message.status = Message.Status.FAILED
            message.raw_payload = {"error": result.error_message}
            message.save(update_fields=["status", "raw_payload", "updated_at"])
            raise MessageSendError(result.error_message)

        message.status_updated_at = timezone.now()
        message.save(update_fields=[
            "status",
            "provider_message_id",
            "raw_payload",
            "status_updated_at",
            "updated_at",
        ])

        conversation.update_last_message_time()

        try:
            builder = EventBuilder()
            dispatcher = WebhookDispatcher()
            payload = builder.message_sent(message)
            dispatcher.dispatch(
                business_id=str(conversation.business.id),
                event_type="message.sent",
                payload=payload,
            )
        except Exception as exc:
            logger.warning(
                "Outbound webhook dispatch failed (non-fatal): %s", exc
            )

        return message

    # ── ASYNC SEND (CELERY) ───────────────────────────────────────────────────
    def send_message_async(
        self,
        business_id: str,
        to_number: str,
        body: str,
        message_type: str = "text",
    ) -> dict:

        from ..tasks import send_whatsapp_message_task

        business = self._get_business(business_id)
        to_number = self._normalize_phone(to_number)

        contact = self._get_or_create_contact(business, to_number)
        conversation = self._get_or_create_conversation(business, contact)

        message = Message.objects.create(
            conversation=conversation,
            direction=Message.Direction.OUTBOUND,
            message_type=message_type,
            body=body,
            status=Message.Status.PENDING,
        )

        task = send_whatsapp_message_task.apply_async(
            kwargs={
                "business_id": business_id,
                "to_number": to_number,
                "body": body,
                "message_type": message_type,
                "message_id": str(message.id),
            },
            queue="messages",
        )

        logger.info(
            "Message queued | message_id=%s | task_id=%s | to=%s",
            message.id, task.id, to_number,
        )

        return {
            "message_id": str(message.id),
            "task_id": task.id,
            "status": "queued",
            "to_number": to_number,
        }

    # ── CALLED BY CELERY WORKER ──────────────────────────────────────────────
    def _call_provider_and_update(self, message) -> bool:
        # ✅ FIX 1 — Idempotency guard (prevents duplicate sends on retry)
        if message.status != Message.Status.PENDING:
            logger.warning(
                "Skipping provider call, message already processed | id=%s | status=%s",
                message.id, message.status
            )
            return True

        client = get_whatsapp_client()

        result = client.send_text_message(
            to_number=message.conversation.contact.phone_number,
            body=message.body,
            from_number=message.conversation.business.phone_number_id,
        )

        if result.success:
            message.status = Message.Status.SENT
            message.provider_message_id = result.provider_message_id
            message.raw_payload = result.raw_response
        else:
            message.status = Message.Status.FAILED
            message.raw_payload = {"error": result.error_message}
            message.save(update_fields=["status", "raw_payload", "updated_at"])
            raise MessageSendError(result.error_message)

        message.status_updated_at = timezone.now()
        message.save(update_fields=[
            "status",
            "provider_message_id",
            "raw_payload",
            "status_updated_at",
            "updated_at",
        ])

        message.conversation.update_last_message_time()

        # ✅ FIX 2 — Webhook for async path
        try:
            builder = EventBuilder()
            dispatcher = WebhookDispatcher()
            payload = builder.message_sent(message)
            dispatcher.dispatch(
                business_id=str(message.conversation.business.id),
                event_type="message.sent",
                payload=payload,
            )
        except Exception as exc:
            logger.warning(
                "Outbound webhook dispatch failed (async, non-fatal): %s", exc
            )

        return True

    # ── STATUS CALLBACK ──────────────────────────────────────────────────────
    def update_message_status(self, provider_message_id: str, new_status: str):
        try:
            message = Message.objects.get(provider_message_id=provider_message_id)
            message.status = new_status
            message.status_updated_at = timezone.now()
            message.save(update_fields=["status", "status_updated_at", "updated_at"])

            # ✅ FIX 3 — Keep conversation ordering correct
            message.conversation.update_last_message_time()

            try:
                STATUS_EVENT_MAP = {
                    "delivered": "message.delivered",
                    "read": "message.read",
                    "failed": "message.failed",
                }
                event_type = STATUS_EVENT_MAP.get(new_status)
                if event_type:
                    builder = EventBuilder()
                    dispatcher = WebhookDispatcher()
                    payload = builder.message_status_changed(message, event_type)
                    dispatcher.dispatch(
                        business_id=str(message.conversation.business_id),
                        event_type=event_type,
                        payload=payload,
                    )
            except Exception as exc:
                logger.warning(
                    "Status webhook dispatch failed (non-fatal): %s", exc
                )

            return message

        except Message.DoesNotExist:
            logger.warning("Unknown provider_message_id: %s", provider_message_id)
            return None

    # ── HELPERS ──────────────────────────────────────────────────────────────
    def _get_business(self, business_id: str) -> BusinessAccount:
        try:
            return BusinessAccount.objects.get(id=business_id, is_active=True)
        except BusinessAccount.DoesNotExist:
            raise MessageSendError(f"BusinessAccount not found: {business_id}")

    def _normalize_phone(self, phone: str) -> str:
        phone = phone.strip().replace(" ", "")
        if not phone.startswith("+"):
            phone = f"+{phone}"
        return phone

    def _get_or_create_contact(self, business, phone_number):
        contact, created = WhatsAppContact.objects.get_or_create(
            business=business,
            phone_number=phone_number,
            defaults={"display_name": ""},
        )
        return contact

    def _get_or_create_conversation(self, business, contact):
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