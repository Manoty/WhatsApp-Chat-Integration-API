import logging
from django.utils import timezone
from ..models import BusinessAccount, WhatsAppContact, Conversation, Message
from .whatsapp_client import get_whatsapp_client

logger = logging.getLogger(__name__)


class MessageSendError(Exception):
    """Raised when a message cannot be sent."""
    pass


class MessageService:
    """
    Handles sending outbound WhatsApp messages.
    Responsibilities:
      - Resolve BusinessAccount
      - Get or create Contact + Conversation
      - Call the WhatsApp provider
      - Store the outbound Message with correct status
      - Handle failures gracefully
    """

    def send_message(
        self,
        business_id: str,
        to_number: str,
        body: str,
        message_type: str = Message.MessageType.TEXT,
    ) -> Message:
        """
        Send a WhatsApp message from a BusinessAccount to a phone number.
        Returns the stored Message object.
        Raises MessageSendError on unrecoverable failures.
        """

        # ── 1. Resolve Business ───────────────────────────────────────────────
        business = self._get_business(business_id)

        # ── 2. Normalize phone number ─────────────────────────────────────────
        to_number = self._normalize_phone(to_number)

        # ── 3. Get or create Contact ──────────────────────────────────────────
        contact = self._get_or_create_contact(business, to_number)

        # ── 4. Get or create Conversation ─────────────────────────────────────
        conversation = self._get_or_create_conversation(business, contact)

        # ── 5. Create Message record as PENDING ───────────────────────────────
        message = Message.objects.create(
            conversation=conversation,
            direction=Message.Direction.OUTBOUND,
            message_type=message_type,
            body=body,
            status=Message.Status.PENDING,
        )

        # ── 6. Call WhatsApp Provider ─────────────────────────────────────────
        client = get_whatsapp_client()
        result = client.send_text_message(
            to_number=to_number,
            body=body,
            from_number=business.phone_number_id,
        )

        # ── 7. Update Message with provider result ────────────────────────────
        if result.success:
            message.status = Message.Status.SENT
            message.provider_message_id = result.provider_message_id
            message.raw_payload = result.raw_response
            message.status_updated_at = timezone.now()
            message.save(update_fields=[
                "status", "provider_message_id",
                "raw_payload", "status_updated_at", "updated_at",
            ])
            conversation.update_last_message_time()
            logger.info(
                "Outbound message sent | id=%s | to=%s | sid=%s",
                message.id, to_number, result.provider_message_id,
            )
        else:
            message.status = Message.Status.FAILED
            message.raw_payload = {"error": result.error_message}
            message.status_updated_at = timezone.now()
            message.save(update_fields=[
                "status", "raw_payload",
                "status_updated_at", "updated_at",
            ])
            logger.error(
                "Outbound message failed | id=%s | to=%s | error=%s",
                message.id, to_number, result.error_message,
            )
            raise MessageSendError(
                f"Provider rejected message: {result.error_message}"
            )

        return message

    def update_message_status(
        self,
        provider_message_id: str,
        new_status: str,
    ) -> Message | None:
        """
        Called when WhatsApp sends a status callback (delivered, read, failed).
        Updates the stored message status.
        """
        try:
            message = Message.objects.get(provider_message_id=provider_message_id)
            message.status = new_status
            message.status_updated_at = timezone.now()
            message.save(update_fields=["status", "status_updated_at", "updated_at"])
            logger.info(
                "Message status updated | id=%s | status=%s",
                message.id, new_status,
            )
            return message
        except Message.DoesNotExist:
            logger.warning(
                "Status update for unknown provider_message_id: %s", provider_message_id
            )
            return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_business(self, business_id: str) -> BusinessAccount:
        try:
            return BusinessAccount.objects.get(id=business_id, is_active=True)
        except BusinessAccount.DoesNotExist:
            raise MessageSendError(f"BusinessAccount not found or inactive: {business_id}")

    def _normalize_phone(self, phone: str) -> str:
        """Ensure E.164 format — strip spaces, ensure leading +."""
        phone = phone.strip().replace(" ", "")
        if not phone.startswith("+"):
            phone = f"+{phone}"
        return phone

    def _get_or_create_contact(
        self, business: BusinessAccount, phone_number: str
    ) -> WhatsAppContact:
        contact, created = WhatsAppContact.objects.get_or_create(
            business=business,
            phone_number=phone_number,
            defaults={"display_name": ""},
        )
        if created:
            logger.info("New contact created via outbound: %s", phone_number)
        return contact

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
            logger.info("New conversation opened for outbound: %s", contact.phone_number)

        return conversation