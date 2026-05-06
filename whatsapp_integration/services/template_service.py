import logging
import re
from django.utils import timezone
from ..models import (
    MessageTemplate, TemplateSend, Message,
    BusinessAccount, WhatsAppContact, Conversation,
)
from .whatsapp_client import get_whatsapp_client
from .message_service import MessageService

logger = logging.getLogger(__name__)


class TemplateError(Exception):
    """Raised when a template operation fails."""
    pass


class TemplateService:
    """
    Handles the full template lifecycle:
      - Validation before send
      - Variable rendering
      - Provider API call
      - TemplateSend record creation and status tracking
      - Analytics increment
    """

    def __init__(self):
        self.message_service = MessageService()

    # ── Send a Template ───────────────────────────────────────────────────────

    def send_template(
        self,
        business_id: str,
        to_number: str,
        template_name: str,
        variables: list[str],
        language: str = "en",
    ) -> TemplateSend:
        """
        Send an approved WhatsApp template to a contact.
        Returns a TemplateSend record.
        Raises TemplateError on failure.
        """

        # ── 1. Resolve business + template ───────────────────────────────────
        business = self._get_business(business_id)
        template = self._get_template(business, template_name, language)
        to_number = self.message_service._normalize_phone(to_number)

        # ── 2. Validate variables ─────────────────────────────────────────────
        self._validate_variables(template, variables)

        # ── 3. Render body ────────────────────────────────────────────────────
        rendered_body = template.render(variables)

        # ── 4. Get or create Contact + Conversation ───────────────────────────
        contact = self.message_service._get_or_create_contact(business, to_number)
        conversation = self.message_service._get_or_create_conversation(
            business, contact
        )

        # ── 5. Create Message record ──────────────────────────────────────────
        message = Message.objects.create(
            conversation=conversation,
            direction=Message.Direction.OUTBOUND,
            message_type=Message.MessageType.TEMPLATE,
            body=rendered_body,
            status=Message.Status.PENDING,
        )

        # ── 6. Create TemplateSend record ─────────────────────────────────────
        template_send = TemplateSend.objects.create(
            template=template,
            message=message,
            contact=contact,
            variables=variables,
            rendered_body=rendered_body,
            status=TemplateSend.Status.QUEUED,
        )

        # ── 7. Call provider ──────────────────────────────────────────────────
        client = get_whatsapp_client()
        result = client.send_template_message(
            to_number=to_number,
            from_number=business.phone_number_id,
            template_name=template.template_name,
            language=template.language,
            variables=variables,
            rendered_body=rendered_body,
        )

        # ── 8. Update records with result ─────────────────────────────────────
        now = timezone.now()
        if result.success:
            message.status              = Message.Status.SENT
            message.provider_message_id = result.provider_message_id
            message.raw_payload         = result.raw_response
            message.status_updated_at   = now
            message.save(update_fields=[
                "status", "provider_message_id",
                "raw_payload", "status_updated_at", "updated_at",
            ])

            template_send.status              = TemplateSend.Status.SENT
            template_send.provider_message_id = result.provider_message_id
            template_send.sent_at             = now
            template_send.save(update_fields=[
                "status", "provider_message_id", "sent_at", "updated_at",
            ])

            conversation.update_last_message_time()
            template.increment_send_count(success=True)

            logger.info(
                "Template sent | template=%s | to=%s | sid=%s",
                template.template_name, to_number, result.provider_message_id,
            )
        else:
            message.status      = Message.Status.FAILED
            message.raw_payload = {"error": result.error_message}
            message.status_updated_at = now
            message.save(update_fields=[
                "status", "raw_payload", "status_updated_at", "updated_at",
            ])

            template_send.status        = TemplateSend.Status.FAILED
            template_send.error_message = result.error_message
            template_send.save(update_fields=[
                "status", "error_message", "updated_at",
            ])

            template.increment_send_count(success=False)

            logger.error(
                "Template send failed | template=%s | to=%s | error=%s",
                template.template_name, to_number, result.error_message,
            )
            raise TemplateError(
                f"Provider rejected template send: {result.error_message}"
            )

        return template_send

    # ── Bulk Send ─────────────────────────────────────────────────────────────

    def queue_bulk_send(
        self,
        business_id: str,
        template_name: str,
        language: str,
        recipients: list[dict],
    ) -> dict:
        """
        Queue template sends for multiple recipients via Celery.

        recipients format:
        [
            {"to_number": "+254712345678", "variables": ["John", "ORD-001"]},
            {"to_number": "+254798765432", "variables": ["Jane", "ORD-002"]},
        ]

        Returns task group info.
        """
        from ..tasks import send_template_task
        from celery import group

        tasks = group(
            send_template_task.s(
                business_id=business_id,
                to_number=r["to_number"],
                template_name=template_name,
                variables=r.get("variables", []),
                language=language,
            )
            for r in recipients
        )

        result = tasks.apply_async(queue="messages")

        logger.info(
            "Bulk template send queued | template=%s | recipients=%d | group_id=%s",
            template_name, len(recipients), result.id,
        )

        return {
            "group_id":        result.id,
            "template_name":   template_name,
            "recipient_count": len(recipients),
            "status":          "queued",
        }

    # ── Mock Submission (dev) / Real submission (prod) ────────────────────────

    def submit_for_approval(self, template: MessageTemplate) -> MessageTemplate:
        """
        In production: calls Meta Graph API to submit template for review.
        In mock mode: auto-approves immediately.
        """
        from django.conf import settings

        if getattr(settings, "WHATSAPP_MOCK_MODE", True):
            # Auto-approve in dev/mock mode
            template.status               = MessageTemplate.Status.APPROVED
            template.provider_template_id = f"MOCK_TPL_{template.id.hex[:12].upper()}"
            template.save(update_fields=[
                "status", "provider_template_id", "updated_at",
            ])
            logger.info(
                "[MOCK] Template auto-approved | name=%s | id=%s",
                template.template_name, template.provider_template_id,
            )
        else:
            # TODO: implement real Meta Graph API call
            template.status = MessageTemplate.Status.PENDING
            template.save(update_fields=["status", "updated_at"])
            logger.info(
                "Template submitted for Meta review | name=%s",
                template.template_name,
            )

        return template

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_business(self, business_id: str) -> BusinessAccount:
        try:
            return BusinessAccount.objects.get(id=business_id, is_active=True)
        except BusinessAccount.DoesNotExist:
            raise TemplateError(
                f"BusinessAccount not found or inactive: {business_id}"
            )

    def _get_template(
        self, business: BusinessAccount, template_name: str, language: str
    ) -> MessageTemplate:
        try:
            template = MessageTemplate.objects.get(
                business=business,
                template_name=template_name,
                language=language,
            )
        except MessageTemplate.DoesNotExist:
            raise TemplateError(
                f"Template '{template_name}' ({language}) not found "
                f"for business '{business.name}'"
            )

        if template.status != MessageTemplate.Status.APPROVED:
            raise TemplateError(
                f"Template '{template_name}' is not approved "
                f"(current status: {template.status}). "
                "Only APPROVED templates can be sent."
            )

        return template

    def _validate_variables(
        self, template: MessageTemplate, variables: list[str]
    ) -> None:
        if len(variables) < template.variable_count:
            raise TemplateError(
                f"Template '{template.template_name}' requires "
                f"{template.variable_count} variable(s), "
                f"but {len(variables)} were provided."
            )