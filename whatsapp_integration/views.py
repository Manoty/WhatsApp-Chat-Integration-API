import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import BusinessAccount, WhatsAppContact, Conversation, Message
from .services.webhook_service import WebhookService

from .serializers import SendMessageRequestSerializer
from .services.message_service import MessageService, MessageSendError

from .models import Conversation, WhatsAppContact
from .serializers import (
    ConversationSerializer,
    MessageSerializer,
    WhatsAppContactSerializer,
)

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

# ─── Send Message ─────────────────────────────────────────────────────────────

@api_view(["POST"])
@permission_classes([AllowAny])
def send_message(request):
    """
    Send a WhatsApp message programmatically.

    POST /api/messages/send/
    {
        "business_id": "<uuid>",
        "to_number": "+254712345678",
        "body": "Hello from the API!"
    }
    """
    serializer = SendMessageRequestSerializer(data=request.data)

    if not serializer.is_valid():
        return Response(
            {"status": "error", "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    validated = serializer.validated_data

    try:
        service = MessageService()
        message = service.send_message(
            business_id=str(validated["business_id"]),
            to_number=validated["to_number"],
            body=validated["body"],
            message_type=validated.get("message_type", "text"),
        )

        return Response(
            {
                "status": "sent",
                "message_id": str(message.id),
                "conversation_id": str(message.conversation.id),
                "provider_message_id": message.provider_message_id,
                "to_number": message.conversation.contact.phone_number,
                "body": message.body,
                "created_at": message.created_at.isoformat(),
            },
            status=status.HTTP_200_OK,
        )

    except MessageSendError as exc:
        return Response(
            {"status": "error", "message": str(exc)},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as exc:
        logger.exception("Unexpected error in send_message view: %s", exc)
        return Response(
            {"status": "error", "message": "Internal server error"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


# ─── Status Callback ──────────────────────────────────────────────────────────

@csrf_exempt
@api_view(["POST"])
@permission_classes([AllowAny])
def message_status_callback(request):
    """
    Twilio calls this URL when a message status changes
    (sent → delivered → read).

    Configure in Twilio console as your Status Callback URL.
    POST /api/messages/status/
    """
    payload = request.data
    provider_message_id = payload.get("MessageSid", "")
    raw_status = payload.get("MessageStatus", "")

    STATUS_MAP = {
        "sent": Message.Status.SENT,
        "delivered": Message.Status.DELIVERED,
        "read": Message.Status.READ,
        "failed": Message.Status.FAILED,
        "undelivered": Message.Status.FAILED,
    }

    new_status = STATUS_MAP.get(raw_status)

    if not provider_message_id or not new_status:
        logger.warning("Invalid status callback payload: %s", payload)
        return Response({"status": "ignored"}, status=200)

    service = MessageService()
    message = service.update_message_status(provider_message_id, new_status)

    if message:
        return Response({
            "status": "updated",
            "message_id": str(message.id),
            "new_status": message.status,
        })

    return Response({"status": "not_found"}, status=200)


# ─── Conversations ────────────────────────────────────────────────────────────

@api_view(["GET"])
def conversation_list(request):
    """
    List all conversations with filtering support.

    GET /api/conversations/
    Query params:
      ?business_id=<uuid>     filter by business
      ?status=open|closed     filter by status
      ?phone=+254712345678    filter by contact phone number
      ?page=1                 pagination (20 per page)
    """
    queryset = Conversation.objects.select_related(
        "business", "contact"
    ).prefetch_related("messages")

    # ── Filters ───────────────────────────────────────────────────────────────
    business_id = request.GET.get("business_id")
    if business_id:
        queryset = queryset.filter(business__id=business_id)

    conv_status = request.GET.get("status")
    if conv_status:
        queryset = queryset.filter(status=conv_status)

    phone = request.GET.get("phone")
    if phone:
        queryset = queryset.filter(contact__phone_number__icontains=phone)

    # ── Pagination ────────────────────────────────────────────────────────────
    page, page_size = _get_pagination(request)
    total = queryset.count()
    start = (page - 1) * page_size
    end = start + page_size
    conversations = queryset[start:end]

    return Response({
        "count": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
        "results": ConversationSerializer(conversations, many=True).data,
    })


@api_view(["GET", "PATCH"])
def conversation_detail(request, conversation_id):
    """
    Retrieve or update a single conversation.

    GET  /api/conversations/<id>/
    PATCH /api/conversations/<id>/   — update status or assigned_to
    {
        "status": "closed",
        "assigned_to": "agent@example.com"
    }
    """
    try:
        conversation = Conversation.objects.select_related(
            "business", "contact"
        ).prefetch_related("messages").get(id=conversation_id)
    except Conversation.DoesNotExist:
        return Response(
            {"error": "Conversation not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    if request.method == "PATCH":
        allowed_fields = {"status", "assigned_to"}
        updates = {k: v for k, v in request.data.items() if k in allowed_fields}

        if "status" in updates:
            valid_statuses = [s.value for s in Conversation.Status]
            if updates["status"] not in valid_statuses:
                return Response(
                    {"error": f"Invalid status. Choose from: {valid_statuses}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        for field, value in updates.items():
            setattr(conversation, field, value)
        conversation.save(update_fields=list(updates.keys()) + ["updated_at"])
        logger.info("Conversation %s updated: %s", conversation_id, updates)

    return Response(ConversationSerializer(conversation).data)


@api_view(["GET"])
def conversation_messages(request, conversation_id):
    """
    Get all messages in a conversation — the full chat thread.

    GET /api/conversations/<id>/messages/
    Query params:
      ?direction=inbound|outbound    filter by direction
      ?page=1                        pagination (50 per page)
    """
    try:
        conversation = Conversation.objects.select_related(
            "business", "contact"
        ).get(id=conversation_id)
    except Conversation.DoesNotExist:
        return Response(
            {"error": "Conversation not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    messages_qs = conversation.messages.all().order_by("created_at")

    # ── Direction filter ──────────────────────────────────────────────────────
    direction = request.GET.get("direction")
    if direction in ["inbound", "outbound"]:
        messages_qs = messages_qs.filter(direction=direction)

    # ── Pagination ────────────────────────────────────────────────────────────
    page, page_size = _get_pagination(request, default_size=50)
    total = messages_qs.count()
    start = (page - 1) * page_size
    end = start + page_size
    messages = messages_qs[start:end]

    return Response({
        "conversation": {
            "id": str(conversation.id),
            "status": conversation.status,
            "contact": conversation.contact.phone_number,
            "contact_name": conversation.contact.display_name,
            "business": conversation.business.name,
        },
        "count": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
        "results": MessageSerializer(messages, many=True).data,
    })


# ─── Contacts ─────────────────────────────────────────────────────────────────

@api_view(["GET"])
def contact_list(request):
    """
    List all contacts.

    GET /api/contacts/
    Query params:
      ?business_id=<uuid>     filter by business
      ?phone=+254             search by phone prefix
      ?name=john              search by display name
      ?page=1
    """
    queryset = WhatsAppContact.objects.select_related("business").all()

    business_id = request.GET.get("business_id")
    if business_id:
        queryset = queryset.filter(business__id=business_id)

    phone = request.GET.get("phone")
    if phone:
        queryset = queryset.filter(phone_number__icontains=phone)

    name = request.GET.get("name")
    if name:
        queryset = queryset.filter(display_name__icontains=name)

    page, page_size = _get_pagination(request)
    total = queryset.count()
    start = (page - 1) * page_size
    end = start + page_size
    contacts = queryset[start:end]

    return Response({
        "count": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
        "results": WhatsAppContactSerializer(contacts, many=True).data,
    })


@api_view(["GET"])
def contact_detail(request, contact_id):
    """
    Get a single contact and all their conversations.

    GET /api/contacts/<id>/
    """
    try:
        contact = WhatsAppContact.objects.select_related("business").get(id=contact_id)
    except WhatsAppContact.DoesNotExist:
        return Response(
            {"error": "Contact not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    conversations = Conversation.objects.filter(
        contact=contact
    ).prefetch_related("messages").order_by("-last_message_at")

    return Response({
        "contact": WhatsAppContactSerializer(contact).data,
        "conversations": ConversationSerializer(conversations, many=True).data,
        "total_messages": Message.objects.filter(
            conversation__contact=contact
        ).count(),
    })


# ─── Shared Pagination Helper ─────────────────────────────────────────────────

def _get_pagination(request, default_size: int = 20) -> tuple[int, int]:
    """Extract and clamp page + page_size from query params."""
    try:
        page = max(1, int(request.GET.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
    try:
        page_size = min(100, max(1, int(request.GET.get("page_size", default_size))))
    except (ValueError, TypeError):
        page_size = default_size
    return page, page_size