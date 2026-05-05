import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import BusinessAccount, WhatsAppContact, Conversation, Message
from .services.webhook_service import WebhookService

logger = logging.getLogger(__name__)


# ─── Health Check ─────────────────────────────────────────────────────────────

@api_view(["GET"])
def health_check(request):
    return Response({
        "status": "ok",
        "service": "WhatsApp Chat Integration API",
        "version": "1.0.0",
        "timestamp": timezone.now().isoformat(),
    })


# ─── System Stats ─────────────────────────────────────────────────────────────

@api_view(["GET"])
def system_stats(request):
    return Response({
        "business_accounts": BusinessAccount.objects.count(),
        "contacts": WhatsAppContact.objects.count(),
        "conversations": Conversation.objects.count(),
        "messages": Message.objects.count(),
    })


# ─── Webhook Receiver ─────────────────────────────────────────────────────────

@csrf_exempt
@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def webhook_receiver(request):
    """
    Unified webhook endpoint for WhatsApp messages.

    GET  — WhatsApp/Twilio challenge verification (required for Meta API setup)
    POST — Incoming message from WhatsApp user
    """

    # ── GET: Webhook Verification (Meta sends this once during setup) ──────────
    if request.method == "GET":
        return _handle_verification(request)

    # ── POST: Incoming Message ────────────────────────────────────────────────
    return _handle_incoming_message(request)


def _handle_verification(request):
    """
    Meta WhatsApp Business API sends a GET with these params to verify
    the webhook URL is under our control.
    """
    mode = request.GET.get("hub.mode")
    token = request.GET.get("hub.verify_token")
    challenge = request.GET.get("hub.challenge")

    from django.conf import settings
    expected_token = getattr(settings, "WHATSAPP_VERIFY_TOKEN", "my_verify_token")

    if mode == "subscribe" and token == expected_token:
        logger.info("Webhook verification successful")
        # Must return the challenge as plain text (not JSON)
        from django.http import HttpResponse
        return HttpResponse(challenge, content_type="text/plain", status=200)

    logger.warning("Webhook verification failed — token mismatch")
    return Response({"error": "Verification failed"}, status=status.HTTP_403_FORBIDDEN)


def _handle_incoming_message(request):
    """
    Process an incoming WhatsApp message POST payload.
    Always returns 200 immediately — WhatsApp will retry on any other code.
    """
    payload = request.data

    if not payload:
        logger.warning("Empty webhook payload received")
        return Response({"status": "ignored", "reason": "empty payload"}, status=200)

    # Detect provider from payload shape
    source = _detect_source(payload)
    logger.info("Webhook received | source=%s | keys=%s", source, list(payload.keys()))

    service = WebhookService()
    message = service.process_incoming_message(payload, source=source)

    if message:
        return Response({
            "status": "received",
            "message_id": str(message.id),
            "conversation_id": str(message.conversation.id),
            "direction": message.direction,
            "source": source,
        }, status=status.HTTP_200_OK)

    # Still 200 — could be a duplicate or status update event
    return Response({
        "status": "ignored",
        "reason": "duplicate, unrecognized format, or no matching business",
    }, status=status.HTTP_200_OK)


def _detect_source(payload: dict) -> str:
    """
    Heuristically detect whether the payload is from Twilio or Meta.
    Twilio payloads contain 'MessageSid'; Meta payloads contain 'object'.
    """
    if "MessageSid" in payload or "From" in payload:
        return "twilio"
    if "object" in payload and "entry" in payload:
        return "meta"
    return "twilio"  # default fallback