import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SendResult:
    """
    Normalized result returned by any WhatsApp provider.
    Decouples the rest of the system from provider-specific responses.
    """
    success: bool
    provider_message_id: str = ""
    error_message: str = ""
    raw_response: dict = None

    def __post_init__(self):
        if self.raw_response is None:
            self.raw_response = {}


class MockWhatsAppClient:
    """
    Fake WhatsApp client for local development and testing.
    Simulates successful sends without hitting any external API.
    Activated when WHATSAPP_MOCK_MODE=True in settings.
    """

    def send_text_message(self, to_number: str, body: str, from_number: str) -> SendResult:
        import uuid
        fake_sid = f"MOCK_SM{uuid.uuid4().hex[:20].upper()}"
        logger.info(
            "[MOCK] Sending message | to=%s | from=%s | body='%s' | sid=%s",
            to_number, from_number, body[:60], fake_sid,
        )
        return SendResult(
            success=True,
            provider_message_id=fake_sid,
            raw_response={
                "mock": True,
                "to": to_number,
                "from": from_number,
                "body": body,
                "sid": fake_sid,
            },
        )
        
    def send_media_message(
        self,
        to_number: str,
        from_number: str,
        media_url: str,
        caption: str = "",
        media_type: str = "image",
    ) -> SendResult:
        import uuid
        fake_sid = f"MOCK_MM{uuid.uuid4().hex[:20].upper()}"
        logger.info(
            "[MOCK] Sending media | to=%s | type=%s | url=%s | sid=%s",
            to_number, media_type, media_url[:60], fake_sid,
        )
        return SendResult(
            success=True,
            provider_message_id=fake_sid,
            raw_response={
                "mock": True,
                "to": to_number,
                "media_url": media_url,
                "caption": caption,
                "media_type": media_type,
                "sid": fake_sid,
            },
        )  
    
    def send_template_message(
        self,
        to_number: str,
        from_number: str,
        template_name: str,
        language: str,
        variables: list,
        rendered_body: str = "",
    ) -> SendResult:
        import uuid
        fake_sid = f"MOCK_TP{uuid.uuid4().hex[:20].upper()}"
        logger.info(
            "[MOCK] Sending template | to=%s | template=%s | lang=%s | sid=%s",
            to_number, template_name, language, fake_sid,
        )
        return SendResult(
            success=True,
            provider_message_id=fake_sid,
            raw_response={
                "mock":          True,
                "to":            to_number,
                "template_name": template_name,
                "language":      language,
                "variables":     variables,
                "rendered_body": rendered_body,
                "sid":           fake_sid,
            },
        )      


class TwilioWhatsAppClient:
    """
    Real Twilio WhatsApp client.
    Requires TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN in environment.
    """

    def __init__(self):
        self.account_sid = os.getenv("TWILIO_ACCOUNT_SID", "")
        self.auth_token = os.getenv("TWILIO_AUTH_TOKEN", "")
        self._client = None

    def _get_client(self):
        if not self._client:
            try:
                from twilio.rest import Client
                self._client = Client(self.account_sid, self.auth_token)
            except ImportError:
                raise RuntimeError(
                    "Twilio package not installed. Run: pip install twilio"
                )
        return self._client

    def send_text_message(self, to_number: str, body: str, from_number: str) -> SendResult:
        try:
            client = self._get_client()
            message = client.messages.create(
                body=body,
                from_=f"whatsapp:{from_number}",
                to=f"whatsapp:{to_number}",
            )
            logger.info(
                "[TWILIO] Message sent | sid=%s | to=%s | status=%s",
                message.sid, to_number, message.status,
            )
            return SendResult(
                success=True,
                provider_message_id=message.sid,
                raw_response={
                    "sid": message.sid,
                    "status": message.status,
                    "to": message.to,
                    "from": message.from_,
                },
            )
        except Exception as exc:
            logger.error("[TWILIO] Send failed | to=%s | error=%s", to_number, exc)
            return SendResult(
                success=False,
                error_message=str(exc),
            )
            
    def send_media_message(
        self,
        to_number: str,
        from_number: str,
        media_url: str,
        caption: str = "",
        media_type: str = "image",
    ) -> SendResult:
        try:
            client = self._get_client()
            message = client.messages.create(
                body=caption,
                from_=f"whatsapp:{from_number}",
                to=f"whatsapp:{to_number}",
                media_url=[media_url],
            )
            logger.info(
                "[TWILIO] Media sent | sid=%s | to=%s | type=%s",
                message.sid, to_number, media_type,
            )
            return SendResult(
                success=True,
                provider_message_id=message.sid,
                raw_response={
                    "sid": message.sid,
                    "status": message.status,
                    "to": message.to,
                    "media_url": media_url,
                },
            )
        except Exception as exc:
            logger.error(
                "[TWILIO] Media send failed | to=%s | error=%s", to_number, exc
            )
            return SendResult(success=False, error_message=str(exc))
        
    def send_template_message(
        self,
        to_number: str,
        from_number: str,
        template_name: str,
        language: str,
        variables: list,
        rendered_body: str = "",
    ) -> SendResult:
        """
        Twilio sends templates as regular messages with pre-approved body text.
        Meta API uses a structured template payload — extend here for Meta.
        """
        try:
            client = self._get_client()
            message = client.messages.create(
                body=rendered_body,
                from_=f"whatsapp:{from_number}",
                to=f"whatsapp:{to_number}",
            )
            logger.info(
                "[TWILIO] Template sent | sid=%s | template=%s | to=%s",
                message.sid, template_name, to_number,
            )
            return SendResult(
                success=True,
                provider_message_id=message.sid,
                raw_response={
                    "sid":           message.sid,
                    "status":        message.status,
                    "template_name": template_name,
                    "to":            message.to,
                },
            )
        except Exception as exc:
            logger.error(
                "[TWILIO] Template send failed | template=%s | to=%s | error=%s",
                template_name, to_number, exc,
            )
            return SendResult(success=False, error_message=str(exc))           
            
            



def get_whatsapp_client():
    """
    Factory — returns mock or real client based on settings.
    Call this everywhere instead of instantiating directly.
    """
    from django.conf import settings
    if getattr(settings, "WHATSAPP_MOCK_MODE", True):
        return MockWhatsAppClient()
    return TwilioWhatsAppClient()