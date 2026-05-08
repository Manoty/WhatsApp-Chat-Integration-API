import logging
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import WebhookEndpoint, WebhookDeliveryLog
from .serializers import WebhookEndpointSerializer, WebhookDeliveryLogSerializer

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
from .models import MessageTemplate, TemplateSend
from .serializers import (
    MessageTemplateSerializer,
    TemplateSendSerializer,
    SendTemplateRequestSerializer,
    BulkSendTemplateRequestSerializer,
)
from .services.template_service import TemplateService, TemplateError

from .models import MediaAttachment
from .serializers import SendMediaRequestSerializer, MediaAttachmentSerializer

from .services.webhook_service import WebhookService
from .services.message_service import MessageService, MessageSendError
from .security import verify_webhook_signature
from .throttles import WebhookRateThrottle, SendMessageRateThrottle

from .models import APIKey
from .serializers import APIKeySerializer, CreateAPIKeySerializer
from .services.api_key_service import APIKeyService

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
        # Dispatch webhook event if conversation was closed
        if updates.get("status") == "closed":
            try:
                from .services.webhook_dispatcher import WebhookDispatcher
                from .services.event_builder import EventBuilder
                builder    = EventBuilder()
                dispatcher = WebhookDispatcher()
                payload    = builder.conversation_closed(conversation)
                dispatcher.dispatch(
                    business_id=str(conversation.business_id),
                    event_type="conversation.closed",
                    payload=payload,
                )
            except Exception as exc:
                logger.warning(
                    "Webhook dispatch failed (non-fatal): %s", exc
                )
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

# ─── Media Messages ───────────────────────────────────────────────────────────

@api_view(["POST"])
@permission_classes([IsAuthenticated])
@throttle_classes([SendMessageRateThrottle])
def send_media_message(request):
    """
    Send a media message (image, audio, video, document) via WhatsApp.

    POST /api/messages/send/media/
    {
        "business_id": "<uuid>",
        "to_number": "+254712345678",
        "media_url": "https://example.com/image.jpg",
        "media_type": "image",
        "caption": "Check out our latest product!"
    }
    """
    serializer = SendMediaRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            {"status": "error", "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    v = serializer.validated_data

    try:
        from .services.message_service import MessageService, MessageSendError
        from .tasks import send_whatsapp_media_task

        svc        = MessageService()
        business   = svc._get_business(str(v["business_id"]))
        to_number  = svc._normalize_phone(v["to_number"])
        contact    = svc._get_or_create_contact(business, to_number)
        conv       = svc._get_or_create_conversation(business, contact)

        # Pre-create Message + MediaAttachment as PENDING
        message = Message.objects.create(
            conversation=conv,
            direction=Message.Direction.OUTBOUND,
            message_type=v["media_type"],
            body=v.get("caption", ""),
            status=Message.Status.PENDING,
        )

        MediaAttachment.objects.create(
            message=message,
            category=v["media_type"],
            media_url=v["media_url"],
            caption=v.get("caption", ""),
        )

        # Queue async task
        task = send_whatsapp_media_task.apply_async(
            kwargs={
                "business_id":  str(v["business_id"]),
                "to_number":    to_number,
                "media_url":    v["media_url"],
                "media_type":   v["media_type"],
                "caption":      v.get("caption", ""),
                "message_id":   str(message.id),
            },
            queue="messages",
        )

        return Response(
            {
                "status":       "queued",
                "message_id":   str(message.id),
                "task_id":      task.id,
                "media_type":   v["media_type"],
                "media_url":    v["media_url"],
                "to_number":    to_number,
                "caption":      v.get("caption", ""),
            },
            status=status.HTTP_202_ACCEPTED,
        )

    except Exception as exc:
        logger.exception("Media send error: %s", exc)
        return Response(
            {"status": "error", "message": str(exc)},
            status=status.HTTP_400_BAD_REQUEST,
        )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def message_media(request, message_id):
    """
    Get the media attachment for a specific message.

    GET /api/messages/<message_id>/media/
    """
    try:
        message = Message.objects.get(id=message_id)
    except Message.DoesNotExist:
        return Response(
            {"error": "Message not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    try:
        attachment = message.media_attachment
    except MediaAttachment.DoesNotExist:
        return Response(
            {"error": "No media attachment on this message"},
            status=status.HTTP_404_NOT_FOUND,
        )

    return Response(MediaAttachmentSerializer(attachment).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def conversation_media(request, conversation_id):
    """
    List all media in a conversation — useful for a media gallery view.

    GET /api/conversations/<id>/media/
    Query params:
      ?category=image|audio|video|document
    """
    try:
        conversation = Conversation.objects.get(id=conversation_id)
    except Conversation.DoesNotExist:
        return Response(
            {"error": "Conversation not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    attachments = MediaAttachment.objects.filter(
        message__conversation=conversation
    ).select_related("message").order_by("-created_at")

    category = request.GET.get("category")
    if category:
        attachments = attachments.filter(category=category)

    page, page_size = _get_pagination(request, default_size=20)
    total  = attachments.count()
    start  = (page - 1) * page_size
    subset = attachments[start:start + page_size]

    return Response({
        "conversation_id": str(conversation_id),
        "count":           total,
        "page":            page,
        "total_pages":     max(1, (total + page_size - 1) // page_size),
        "results":         MediaAttachmentSerializer(subset, many=True).data,
    })
    
# ─── Templates: CRUD ─────────────────────────────────────────────────────────

@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def template_list(request):
    """
    List all templates or create a new one.

    GET  /api/templates/
    POST /api/templates/
    {
        "business": "<uuid>",
        "name": "Order Confirmation",
        "template_name": "order_confirmation",
        "category": "utility",
        "language": "en",
        "body": "Hello {{1}}, your order {{2}} for {{3}} is confirmed! 🎉",
        "footer_text": "Reply STOP to unsubscribe"
    }
    """
    if request.method == "GET":
        qs = MessageTemplate.objects.select_related("business").all()

        business_id = request.GET.get("business_id")
        if business_id:
            qs = qs.filter(business__id=business_id)

        status_filter = request.GET.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)

        category = request.GET.get("category")
        if category:
            qs = qs.filter(category=category)

        return Response(MessageTemplateSerializer(qs, many=True).data)

    serializer = MessageTemplateSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            {"status": "error", "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    template = serializer.save()
    logger.info(
        "Template created | id=%s | name=%s", template.id, template.name
    )
    return Response(
        MessageTemplateSerializer(template).data,
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET", "PUT", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def template_detail(request, template_id):
    """
    Retrieve, update, or delete a single template.

    GET    /api/templates/<id>/
    PATCH  /api/templates/<id>/
    DELETE /api/templates/<id>/
    """
    try:
        template = MessageTemplate.objects.select_related("business").get(
            id=template_id
        )
    except MessageTemplate.DoesNotExist:
        return Response(
            {"error": "Template not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    if request.method == "GET":
        return Response(MessageTemplateSerializer(template).data)

    if request.method == "DELETE":
        if template.status == MessageTemplate.Status.APPROVED:
            return Response(
                {"error": "Cannot delete an approved template. "
                          "Archive it by setting status to 'disabled' instead."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        name = template.name
        template.delete()
        return Response({"status": "deleted", "name": name})

    partial = request.method == "PATCH"
    serializer = MessageTemplateSerializer(
        template, data=request.data, partial=partial
    )
    if serializer.is_valid():
        return Response(
            MessageTemplateSerializer(serializer.save()).data
        )
    return Response(
        {"status": "error", "errors": serializer.errors},
        status=status.HTTP_400_BAD_REQUEST,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def template_submit(request, template_id):
    """
    Submit a DRAFT template for provider approval.
    In mock mode: auto-approves immediately.

    POST /api/templates/<id>/submit/
    """
    try:
        template = MessageTemplate.objects.get(id=template_id)
    except MessageTemplate.DoesNotExist:
        return Response(
            {"error": "Template not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    if template.status not in (
        MessageTemplate.Status.DRAFT,
        MessageTemplate.Status.REJECTED,
    ):
        return Response(
            {"error": f"Only DRAFT or REJECTED templates can be submitted. "
                      f"Current status: {template.status}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    svc = TemplateService()
    template = svc.submit_for_approval(template)

    return Response({
        "status":               "submitted",
        "template_id":          str(template.id),
        "template_status":      template.status,
        "provider_template_id": template.provider_template_id,
    })


# ─── Templates: Send ─────────────────────────────────────────────────────────

@api_view(["POST"])
@permission_classes([IsAuthenticated])
@throttle_classes([SendMessageRateThrottle])
def template_send(request):
    """
    Send an approved template to a single contact.

    POST /api/templates/send/
    {
        "business_id": "<uuid>",
        "to_number": "+254712345678",
        "template_name": "order_confirmation",
        "language": "en",
        "variables": ["John", "ORD-001", "KES 2,500"]
    }
    """
    serializer = SendTemplateRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            {"status": "error", "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    v = serializer.validated_data

    try:
        # Queue as Celery task — returns immediately
        from .tasks import send_template_task
        task = send_template_task.apply_async(
            kwargs={
                "business_id":   str(v["business_id"]),
                "to_number":     v["to_number"],
                "template_name": v["template_name"],
                "variables":     v["variables"],
                "language":      v["language"],
            },
            queue="messages",
        )

        return Response(
            {
                "status":        "queued",
                "task_id":       task.id,
                "template_name": v["template_name"],
                "to_number":     v["to_number"],
                "variables":     v["variables"],
                "note": "Track delivery via /api/tasks/<task_id>/",
            },
            status=status.HTTP_202_ACCEPTED,
        )

    except TemplateError as exc:
        return Response(
            {"status": "error", "message": str(exc)},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as exc:
        logger.exception("Template send error: %s", exc)
        return Response(
            {"status": "error", "message": "Internal server error"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def template_bulk_send(request):
    """
    Send an approved template to multiple contacts via Celery group.
    Max 1,000 recipients per request.

    POST /api/templates/send/bulk/
    {
        "business_id": "<uuid>",
        "template_name": "order_confirmation",
        "language": "en",
        "recipients": [
            {"to_number": "+254712345678", "variables": ["John", "ORD-001", "KES 2,500"]},
            {"to_number": "+254798765432", "variables": ["Jane", "ORD-002", "KES 3,000"]}
        ]
    }
    """
    serializer = BulkSendTemplateRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            {"status": "error", "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    v = serializer.validated_data

    try:
        svc    = TemplateService()
        result = svc.queue_bulk_send(
            business_id=str(v["business_id"]),
            template_name=v["template_name"],
            language=v["language"],
            recipients=v["recipients"],
        )
        return Response(result, status=status.HTTP_202_ACCEPTED)

    except TemplateError as exc:
        return Response(
            {"status": "error", "message": str(exc)},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as exc:
        logger.exception("Bulk template send error: %s", exc)
        return Response(
            {"status": "error", "message": "Internal server error"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def template_send_history(request, template_id):
    """
    Get the send history for a specific template.

    GET /api/templates/<id>/history/
    Query params:
      ?status=sent|delivered|failed
      ?page=1
    """
    try:
        template = MessageTemplate.objects.get(id=template_id)
    except MessageTemplate.DoesNotExist:
        return Response(
            {"error": "Template not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    sends = TemplateSend.objects.filter(
        template=template
    ).select_related("contact").order_by("-created_at")

    status_filter = request.GET.get("status")
    if status_filter:
        sends = sends.filter(status=status_filter)

    page, page_size = _get_pagination(request)
    total  = sends.count()
    start  = (page - 1) * page_size
    subset = sends[start:start + page_size]

    return Response({
        "template":    MessageTemplateSerializer(template).data,
        "count":       total,
        "page":        page,
        "total_pages": max(1, (total + page_size - 1) // page_size),
        "results":     TemplateSendSerializer(subset, many=True).data,
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def template_preview(request):
    """
    Preview a rendered template without sending it.

    POST /api/templates/preview/
    {
        "business_id": "<uuid>",
        "template_name": "order_confirmation",
        "language": "en",
        "variables": ["John", "ORD-001", "KES 2,500"]
    }
    """
    business_id   = request.data.get("business_id")
    template_name = request.data.get("template_name")
    language      = request.data.get("language", "en")
    variables     = request.data.get("variables", [])

    if not business_id or not template_name:
        return Response(
            {"error": "business_id and template_name are required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        business = BusinessAccount.objects.get(
            id=business_id, is_active=True
        )
        template = MessageTemplate.objects.get(
            business=business,
            template_name=template_name,
            language=language,
        )
    except (BusinessAccount.DoesNotExist, MessageTemplate.DoesNotExist) as exc:
        return Response(
            {"error": str(exc)},
            status=status.HTTP_404_NOT_FOUND,
        )

    rendered = template.render(variables)

    return Response({
        "template_name":   template.template_name,
        "language":        template.language,
        "status":          template.status,
        "variable_count":  template.variable_count,
        "variables_given": len(variables),
        "original_body":   template.body,
        "rendered_body":   rendered,
        "ready_to_send":   template.status == MessageTemplate.Status.APPROVED,
    })    
    
# ─── Webhook Endpoints ────────────────────────────────────────────────────────

@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def webhook_endpoint_list(request):
    """
    List all registered webhook endpoints or create a new one.

    GET  /api/webhooks/endpoints/
    POST /api/webhooks/endpoints/
    {
        "business": "<uuid>",
        "name": "CRM Integration",
        "url": "https://mycrm.com/whatsapp/events",
        "secret": "my-secret-key-for-signing",
        "subscribed_events": ["message.received", "conversation.opened"]
    }

    Use ["*"] in subscribed_events to receive ALL event types.
    """
    if request.method == "GET":
        qs = WebhookEndpoint.objects.select_related("business").all()

        business_id = request.GET.get("business_id")
        if business_id:
            qs = qs.filter(business__id=business_id)

        active = request.GET.get("active")
        if active == "true":
            qs = qs.filter(is_active=True)

        return Response(WebhookEndpointSerializer(qs, many=True).data)

    serializer = WebhookEndpointSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            {"status": "error", "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    endpoint = serializer.save()
    logger.info(
        "WebhookEndpoint created | id=%s | url=%s | business=%s",
        endpoint.id, endpoint.url, endpoint.business.name,
    )
    return Response(
        WebhookEndpointSerializer(endpoint).data,
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def webhook_endpoint_detail(request, endpoint_id):
    """
    Retrieve, update or delete a single webhook endpoint.

    GET    /api/webhooks/endpoints/<id>/
    PATCH  /api/webhooks/endpoints/<id>/
    DELETE /api/webhooks/endpoints/<id>/
    """
    try:
        endpoint = WebhookEndpoint.objects.select_related("business").get(
            id=endpoint_id
        )
    except WebhookEndpoint.DoesNotExist:
        return Response(
            {"error": "WebhookEndpoint not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    if request.method == "GET":
        return Response(WebhookEndpointSerializer(endpoint).data)

    if request.method == "DELETE":
        name = endpoint.name
        endpoint.delete()
        logger.info("WebhookEndpoint deleted | name=%s", name)
        return Response({"status": "deleted", "name": name})

    serializer = WebhookEndpointSerializer(
        endpoint, data=request.data, partial=True
    )
    if serializer.is_valid():
        return Response(
            WebhookEndpointSerializer(serializer.save()).data
        )
    return Response(
        {"status": "error", "errors": serializer.errors},
        status=status.HTTP_400_BAD_REQUEST,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def webhook_endpoint_test(request, endpoint_id):
    """
    Send a test ping event to a webhook endpoint.
    Use this to verify your endpoint URL is reachable and
    your signature verification is working correctly.

    POST /api/webhooks/endpoints/<id>/test/
    """
    try:
        endpoint = WebhookEndpoint.objects.get(id=endpoint_id)
    except WebhookEndpoint.DoesNotExist:
        return Response(
            {"error": "WebhookEndpoint not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    from .services.event_builder import EventBuilder
    from .tasks import deliver_webhook_task

    test_payload = EventBuilder().build(
        event_type="ping",
        business_id=str(endpoint.business_id),
        data={
            "message": "This is a test event from WhatsApp Chat API",
            "endpoint_id": str(endpoint.id),
            "endpoint_name": endpoint.name,
        },
    )

    task = deliver_webhook_task.apply_async(
        kwargs={
            "endpoint_id": str(endpoint.id),
            "event_type":  "ping",
            "payload":     test_payload,
            "attempt":     1,
        },
        queue="webhooks",
    )

    return Response({
        "status":   "test_queued",
        "task_id":  task.id,
        "endpoint": endpoint.url,
        "note":     "Check delivery logs to see the result.",
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def webhook_delivery_logs(request, endpoint_id):
    """
    Get delivery logs for a webhook endpoint.

    GET /api/webhooks/endpoints/<id>/logs/
    Query params:
      ?status=success|failed|retrying
      ?event_type=message.received
      ?page=1
    """
    try:
        endpoint = WebhookEndpoint.objects.get(id=endpoint_id)
    except WebhookEndpoint.DoesNotExist:
        return Response(
            {"error": "WebhookEndpoint not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    logs = WebhookDeliveryLog.objects.filter(
        endpoint=endpoint
    ).order_by("-created_at")

    status_filter = request.GET.get("status")
    if status_filter:
        logs = logs.filter(status=status_filter)

    event_type = request.GET.get("event_type")
    if event_type:
        logs = logs.filter(event_type=event_type)

    page, page_size = _get_pagination(request, default_size=20)
    total  = logs.count()
    start  = (page - 1) * page_size
    subset = logs[start:start + page_size]

    return Response({
        "endpoint": {
            "id":               str(endpoint.id),
            "name":             endpoint.name,
            "url":              endpoint.url,
            "total_deliveries": endpoint.total_deliveries,
            "failed_deliveries":endpoint.failed_deliveries,
        },
        "count":       total,
        "page":        page,
        "total_pages": max(1, (total + page_size - 1) // page_size),
        "results":     WebhookDeliveryLogSerializer(subset, many=True).data,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def webhook_event_types(request):
    """
    List all available event types for subscription.

    GET /api/webhooks/events/
    """
    return Response({
        "event_types": [
            {
                "type":        e.value,
                "label":       e.label,
                "description": _event_description(e.value),
            }
            for e in WebhookEndpoint.EventType
        ],
        "wildcard": {
            "type":  "*",
            "label": "All Events",
            "description": "Subscribe to every event type",
        },
    })


def _event_description(event_type: str) -> str:
    return {
        "message.received":    "Fired when a contact sends an inbound message",
        "message.sent":        "Fired when an outbound message is sent",
        "message.delivered":   "Fired when a message is delivered to device",
        "message.read":        "Fired when a contact reads a message",
        "message.failed":      "Fired when a message fails to deliver",
        "conversation.opened": "Fired when a new conversation starts",
        "conversation.closed": "Fired when a conversation is closed",
        "contact.created":     "Fired when a new contact is auto-created",
    }.get(event_type, "")    
    
    
# ─── API Key Management ───────────────────────────────────────────────────────

@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def api_key_list(request):
    """
    List all API keys or create a new one.

    GET  /api/keys/
    POST /api/keys/
    {
        "business_id": "<uuid>",
        "name": "Production App",
        "scope": "write",
        "expiry_at": "2027-01-01T00:00:00Z",
        "allowed_ips": []
    }

    ⚠️  The raw key is returned ONCE on creation.
        Store it securely — it cannot be retrieved again.
    """
    if request.method == "GET":
        qs = APIKey.objects.select_related("business").all()

        business_id = request.GET.get("business_id")
        if business_id:
            qs = qs.filter(business__id=business_id)

        status_filter = request.GET.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)

        scope_filter = request.GET.get("scope")
        if scope_filter:
            qs = qs.filter(scope=scope_filter)

        return Response(APIKeySerializer(qs, many=True).data)

    # POST — create new key
    serializer = CreateAPIKeySerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            {"status": "error", "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    v = serializer.validated_data

    try:
        svc          = APIKeyService()
        api_key, raw = svc.create_key(
            business_id=str(v["business_id"]),
            name=v["name"],
            scope=v.get("scope", APIKey.Scope.WRITE),
            expiry_at=v.get("expiry_at"),
            allowed_ips=v.get("allowed_ips", []),
        )

        return Response(
            {
                "status":  "created",
                "key":     raw,              # ← shown ONCE, store it now
                "warning": "Save this key immediately. It will not be shown again.",
                "api_key": APIKeySerializer(api_key).data,
            },
            status=status.HTTP_201_CREATED,
        )

    except ValueError as exc:
        return Response(
            {"status": "error", "message": str(exc)},
            status=status.HTTP_400_BAD_REQUEST,
        )


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def api_key_detail(request, key_id):
    """
    Retrieve, update, or delete a single API key.

    GET    /api/keys/<id>/
    PATCH  /api/keys/<id>/   — update name, allowed_ips, expiry_at
    DELETE /api/keys/<id>/   — permanently delete (prefer revoke instead)
    """
    try:
        api_key = APIKey.objects.select_related("business").get(id=key_id)
    except APIKey.DoesNotExist:
        return Response(
            {"error": "API key not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    if request.method == "GET":
        return Response(APIKeySerializer(api_key).data)

    if request.method == "DELETE":
        name = api_key.name
        api_key.delete()
        logger.info("API key deleted | name=%s", name)
        return Response({"status": "deleted", "name": name})

    # PATCH — only allow updating safe fields
    allowed = {"name", "expiry_at", "allowed_ips"}
    updates = {k: v for k, v in request.data.items() if k in allowed}

    for field, value in updates.items():
        setattr(api_key, field, value)

    api_key.save(update_fields=list(updates.keys()) + ["updated_at"])
    return Response(APIKeySerializer(api_key).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_key_revoke(request, key_id):
    """
    Immediately revoke an API key.
    The key stops working instantly — no grace period.

    POST /api/keys/<id>/revoke/
    """
    try:
        svc     = APIKeyService()
        api_key = svc.revoke_key(key_id)
        return Response({
            "status":  "revoked",
            "key_id":  str(api_key.id),
            "name":    api_key.name,
            "revoked_at": timezone.now().isoformat(),
        })
    except ValueError as exc:
        return Response(
            {"status": "error", "message": str(exc)},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except APIKey.DoesNotExist:
        return Response(
            {"error": "API key not found"},
            status=status.HTTP_404_NOT_FOUND,
        )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_key_rotate(request, key_id):
    """
    Rotate an API key — generates a new key, revokes the old one.

    POST /api/keys/<id>/rotate/

    ⚠️  The new raw key is returned ONCE.
        The old key stops working immediately.
        Update your application before rotating.
    """
    try:
        svc         = APIKeyService()
        new_key, raw = svc.rotate_key(key_id)
        return Response(
            {
                "status":  "rotated",
                "key":     raw,
                "warning": "Old key is now revoked. Save this new key immediately.",
                "api_key": APIKeySerializer(new_key).data,
            },
            status=status.HTTP_201_CREATED,
        )
    except ValueError as exc:
        return Response(
            {"status": "error", "message": str(exc)},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except APIKey.DoesNotExist:
        return Response(
            {"error": "API key not found"},
            status=status.HTTP_404_NOT_FOUND,
        )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_key_stats(request, key_id):
    """
    Get usage statistics for an API key.

    GET /api/keys/<id>/stats/
    """
    try:
        api_key = APIKey.objects.select_related("business").get(id=key_id)
    except APIKey.DoesNotExist:
        return Response(
            {"error": "API key not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    from django.utils import timezone as tz

    now = tz.now()
    return Response({
        "key_id":          str(api_key.id),
        "name":            api_key.name,
        "prefix":          api_key.prefix,
        "scope":           api_key.scope,
        "status":          api_key.status,
        "request_count":   api_key.request_count,
        "last_used_at":    api_key.last_used_at,
        "created_at":      api_key.created_at,
        "expiry_at":       api_key.expiry_at,
        "days_until_expiry": (
            max(0, (api_key.expiry_at - now).days)
            if api_key.expiry_at else None
        ),
        "allowed_ips":     api_key.allowed_ips,
        "rotated_from":    str(api_key.rotated_from_id)
                           if api_key.rotated_from_id else None,
        "business":        api_key.business.name,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_key_verify(request):
    """
    Verify the current request's API key and return its metadata.
    Useful for client apps to confirm which key they are using.

    GET /api/keys/verify/
    """
    user = request.user
    if not hasattr(user, "api_key"):
        return Response({
            "valid":  True,
            "type":   "legacy",
            "scope":  "admin",
            "note":   "Legacy env-var key — migrate to DB keys",
        })

    api_key = user.api_key
    return Response({
        "valid":          True,
        "key_id":         str(api_key.id),
        "name":           api_key.name,
        "prefix":         api_key.prefix,
        "scope":          api_key.scope,
        "status":         api_key.status,
        "business":       api_key.business.name,
        "request_count":  api_key.request_count,
        "last_used_at":   api_key.last_used_at,
        "expiry_at":      api_key.expiry_at,
    })    