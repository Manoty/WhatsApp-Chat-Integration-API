import logging
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import (
    BusinessAccount, WhatsAppContact,
    Conversation, Message, AutoReplyRule,
)
from .serializers import (
    SendMessageRequestSerializer,
    ConversationSerializer,
    MessageSerializer,
    WhatsAppContactSerializer,
    AutoReplyRuleSerializer,
)
from .services.webhook_service import WebhookService
from .services.message_service import MessageService, MessageSendError
from .security import verify_webhook_signature
from .throttles import WebhookRateThrottle, SendMessageRateThrottle

logger = logging.getLogger(__name__)


# ─── System ───────────────────────────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([AllowAny])
def health_check(request):
    return Response({
        "status": "ok",
        "service": "WhatsApp Chat Integration API",
        "version": "1.0.0",
        "timestamp": timezone.now().isoformat(),
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def system_stats(request):
    return Response({
        "business_accounts": BusinessAccount.objects.count(),
        "contacts": WhatsAppContact.objects.count(),
        "conversations": Conversation.objects.count(),
        "messages": Message.objects.count(),
        "auto_reply_rules": AutoReplyRule.objects.count(),
    })


# ─── Webhook ──────────────────────────────────────────────────────────────────

@csrf_exempt
@api_view(["GET", "POST"])
@permission_classes([AllowAny])
@throttle_classes([WebhookRateThrottle])
def webhook_receiver(request):
    if request.method == "GET":
        return _handle_verification(request)
    return _handle_incoming_message(request)


def _handle_verification(request):
    mode = request.GET.get("hub.mode")
    token = request.GET.get("hub.verify_token")
    challenge = request.GET.get("hub.challenge")
    from django.conf import settings
    expected_token = getattr(settings, "WHATSAPP_VERIFY_TOKEN", "my_verify_token")
    if mode == "subscribe" and token == expected_token:
        logger.info("Webhook verification successful")
        from django.http import HttpResponse
        return HttpResponse(challenge, content_type="text/plain", status=200)
    logger.warning("Webhook verification failed — token mismatch")
    return Response({"error": "Verification failed"}, status=status.HTTP_403_FORBIDDEN)


def _handle_incoming_message(request):
    payload = request.data
    if not payload:
        return Response({"status": "ignored", "reason": "empty payload"}, status=200)

    source = _detect_source(payload)

    # ── Signature verification ────────────────────────────────────────────────
    if not verify_webhook_signature(request, source):
        logger.warning(
            "Webhook signature verification failed",
            extra={"source": source, "ip": _get_ip(request)},
        )
        return Response(
            {"error": "Invalid signature"},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    logger.info(
        "Webhook received",
        extra={
            "source": source,
            "request_id": getattr(request, "request_id", ""),
        },
    )

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

    return Response({
        "status": "ignored",
        "reason": "duplicate, unrecognized format, or no matching business",
    }, status=status.HTTP_200_OK)


def _detect_source(payload: dict) -> str:
    if "MessageSid" in payload or "From" in payload:
        return "twilio"
    if "object" in payload and "entry" in payload:
        return "meta"
    return "twilio"


def _get_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


# ─── Messaging ────────────────────────────────────────────────────────────────

@api_view(["POST"])
@permission_classes([IsAuthenticated])
@throttle_classes([SendMessageRateThrottle])
def send_message(request):
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
        return Response({
            "status": "sent",
            "message_id": str(message.id),
            "conversation_id": str(message.conversation.id),
            "provider_message_id": message.provider_message_id,
            "to_number": message.conversation.contact.phone_number,
            "body": message.body,
            "created_at": message.created_at.isoformat(),
        }, status=status.HTTP_200_OK)
    except MessageSendError as exc:
        return Response(
            {"status": "error", "message": str(exc)},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as exc:
        logger.exception("Unexpected error in send_message: %s", exc)
        return Response(
            {"status": "error", "message": "Internal server error"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@csrf_exempt
@api_view(["POST"])
@permission_classes([AllowAny])
def message_status_callback(request):
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
@permission_classes([IsAuthenticated])
def conversation_list(request):
    queryset = Conversation.objects.select_related(
        "business", "contact"
    ).prefetch_related("messages")

    business_id = request.GET.get("business_id")
    if business_id:
        queryset = queryset.filter(business__id=business_id)

    conv_status = request.GET.get("status")
    if conv_status:
        queryset = queryset.filter(status=conv_status)

    phone = request.GET.get("phone")
    if phone:
        queryset = queryset.filter(contact__phone_number__icontains=phone)

    page, page_size = _get_pagination(request)
    total = queryset.count()
    start = (page - 1) * page_size
    conversations = queryset[start:start + page_size]

    return Response({
        "count": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
        "results": ConversationSerializer(conversations, many=True).data,
    })


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
def conversation_detail(request, conversation_id):
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
    return Response(ConversationSerializer(conversation).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def conversation_messages(request, conversation_id):
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
    direction = request.GET.get("direction")
    if direction in ["inbound", "outbound"]:
        messages_qs = messages_qs.filter(direction=direction)
    page, page_size = _get_pagination(request, default_size=50)
    total = messages_qs.count()
    start = (page - 1) * page_size
    messages = messages_qs[start:start + page_size]
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
@permission_classes([IsAuthenticated])
def contact_list(request):
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
    return Response({
        "count": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
        "results": WhatsAppContactSerializer(queryset[start:start + page_size], many=True).data,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def contact_detail(request, contact_id):
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


# ─── Auto Reply Rules ─────────────────────────────────────────────────────────

@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def auto_reply_rule_list(request):
    if request.method == "GET":
        queryset = AutoReplyRule.objects.select_related("business").all()
        business_id = request.GET.get("business_id")
        if business_id:
            queryset = queryset.filter(business__id=business_id)
        if request.GET.get("active") == "true":
            queryset = queryset.filter(is_active=True)
        return Response(AutoReplyRuleSerializer(queryset, many=True).data)

    serializer = AutoReplyRuleSerializer(data=request.data)
    if serializer.is_valid():
        rule = serializer.save()
        return Response(
            AutoReplyRuleSerializer(rule).data,
            status=status.HTTP_201_CREATED,
        )
    return Response(
        {"status": "error", "errors": serializer.errors},
        status=status.HTTP_400_BAD_REQUEST,
    )


@api_view(["GET", "PUT", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def auto_reply_rule_detail(request, rule_id):
    try:
        rule = AutoReplyRule.objects.select_related("business").get(id=rule_id)
    except AutoReplyRule.DoesNotExist:
        return Response(
            {"error": "AutoReplyRule not found"},
            status=status.HTTP_404_NOT_FOUND,
        )
    if request.method == "GET":
        return Response(AutoReplyRuleSerializer(rule).data)
    if request.method == "DELETE":
        name = rule.name
        rule.delete()
        return Response({"status": "deleted", "name": name})
    partial = request.method == "PATCH"
    serializer = AutoReplyRuleSerializer(rule, data=request.data, partial=partial)
    if serializer.is_valid():
        return Response(AutoReplyRuleSerializer(serializer.save()).data)
    return Response(
        {"status": "error", "errors": serializer.errors},
        status=status.HTTP_400_BAD_REQUEST,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def test_auto_reply(request):
    business_id = request.data.get("business_id")
    message_body = request.data.get("message", "").strip()
    if not business_id or not message_body:
        return Response(
            {"error": "Both business_id and message are required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        business = BusinessAccount.objects.get(id=business_id, is_active=True)
    except BusinessAccount.DoesNotExist:
        return Response(
            {"error": "BusinessAccount not found"},
            status=status.HTTP_404_NOT_FOUND,
        )
    from .services.auto_reply_engine import AutoReplyEngine
    engine = AutoReplyEngine()
    rules = AutoReplyRule.objects.filter(
        business=business, is_active=True, is_fallback=False,
    ).order_by("priority", "created_at")
    matched_rule = None
    for rule in rules:
        if engine._matches(rule, message_body):
            matched_rule = rule
            break
    if not matched_rule:
        matched_rule = AutoReplyRule.objects.filter(
            business=business, is_active=True, is_fallback=True,
        ).order_by("priority").first()
    if matched_rule:
        return Response({
            "matched": True,
            "rule": AutoReplyRuleSerializer(matched_rule).data,
            "would_reply_with": matched_rule.reply_text,
            "is_fallback": matched_rule.is_fallback,
        })
    return Response({
        "matched": False,
        "rule": None,
        "would_reply_with": None,
        "reason": "No matching rule and no fallback configured",
    })


# ─── Pagination Helper ────────────────────────────────────────────────────────

def _get_pagination(request, default_size: int = 20) -> tuple[int, int]:
    try:
        page = max(1, int(request.GET.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
    try:
        page_size = min(100, max(1, int(request.GET.get("page_size", default_size))))
    except (ValueError, TypeError):
        page_size = default_size
    return page, page_size

# ─── Async Send Message ───────────────────────────────────────────────────────

@api_view(["POST"])
@permission_classes([IsAuthenticated])
@throttle_classes([SendMessageRateThrottle])
def send_message_async(request):
    """
    Queue a WhatsApp message send as a Celery task.
    Returns immediately with task_id — message is sent in background.

    POST /api/messages/send/async/
    Same request body as /api/messages/send/
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
        result = service.send_message_async(
            business_id=str(validated["business_id"]),
            to_number=validated["to_number"],
            body=validated["body"],
            message_type=validated.get("message_type", "text"),
        )
        return Response(
            {
                "status": "queued",
                "message_id": result["message_id"],
                "task_id": result["task_id"],
                "to_number": result["to_number"],
                "body": validated["body"],
                "note": "Message queued for async delivery. Track via /api/tasks/<task_id>/",
            },
            status=status.HTTP_202_ACCEPTED,
        )

    except Exception as exc:
        logger.exception("Failed to queue message: %s", exc)
        return Response(
            {"status": "error", "message": str(exc)},
            status=status.HTTP_400_BAD_REQUEST,
        )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def task_status(request, task_id):
    """
    Check the status of a Celery task.

    GET /api/tasks/<task_id>/
    """
    from celery.result import AsyncResult
    result = AsyncResult(task_id)

    response = {
        "task_id": task_id,
        "status": result.status,      # PENDING, STARTED, SUCCESS, FAILURE, RETRY
        "ready": result.ready(),
    }

    if result.ready():
        if result.successful():
            response["result"] = result.result
        else:
            response["error"] = str(result.result)

    return Response(response)