import logging
from celery import shared_task
from celery.utils.log import get_task_logger
from django.utils import timezone
import time
import hashlib
import hmac
import json

logger = get_task_logger(__name__)


# ─── Send Message Task ────────────────────────────────────────────────────────

@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,          # 1 min, then 2 min, then 4 min (exponential)
    name="whatsapp.send_message",
    queue="messages",
)
def send_whatsapp_message_task(
    self,
    business_id: str,
    to_number: str,
    body: str,
    message_type: str = "text",
    message_id: str = None,          # Pre-created Message UUID (optional)
):
    """
    Async task: send a WhatsApp message via the provider.

    Retries up to 3 times on failure with exponential backoff:
      Attempt 1: immediate
      Attempt 2: 60 seconds later
      Attempt 3: 120 seconds later
      Attempt 4: 240 seconds later → mark FAILED
    """
    from .services.message_service import MessageService, MessageSendError
    from .models import Message

    logger.info(
        "Task started | to=%s | attempt=%d/%d",
        to_number,
        self.request.retries + 1,
        self.max_retries + 1,
    )

    try:
        service = MessageService()

        if message_id:
            # Message record already exists — just call the provider
            try:
                message = Message.objects.get(id=message_id)
                result = service._call_provider_and_update(message)
                return {
                    "status": "sent",
                    "message_id": str(message.id),
                    "provider_message_id": message.provider_message_id,
                }
            except Message.DoesNotExist:
                logger.error("Message record not found: %s", message_id)
                return {"status": "error", "reason": "message_not_found"}
        else:
            # Full send flow
            message = service.send_message(
                business_id=business_id,
                to_number=to_number,
                body=body,
                message_type=message_type,
            )
            return {
                "status": "sent",
                "message_id": str(message.id),
                "provider_message_id": message.provider_message_id,
            }

    except MessageSendError as exc:
        logger.warning(
            "Send failed (attempt %d) | error=%s | retrying...",
            self.request.retries + 1,
            exc,
        )
        # Exponential backoff: 60s, 120s, 240s
        retry_delay = 60 * (2 ** self.request.retries)
        raise self.retry(exc=exc, countdown=retry_delay)

    except Exception as exc:
        logger.exception("Unexpected task error: %s", exc)
        raise self.retry(exc=exc, countdown=60)


# ─── Auto Reply Task ──────────────────────────────────────────────────────────

@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    name="whatsapp.auto_reply",
    queue="messages",
)
def process_auto_reply_task(self, message_id: str):
    """
    Async task: run the AutoReplyEngine for a stored inbound message.
    Decouples auto-reply processing from the webhook response cycle.
    """
    from .models import Message
    from .services.auto_reply_engine import AutoReplyEngine

    try:
        message = Message.objects.select_related(
            "conversation__business",
            "conversation__contact",
        ).get(id=message_id)

        engine = AutoReplyEngine()
        reply = engine.process(message)

        if reply:
            logger.info(
                "Auto-reply sent | message_id=%s | reply_id=%s",
                message_id, reply.id,
            )
            return {"status": "replied", "reply_message_id": str(reply.id)}

        return {"status": "no_match"}

    except Message.DoesNotExist:
        logger.error("Message not found for auto-reply: %s", message_id)
        return {"status": "error", "reason": "message_not_found"}

    except Exception as exc:
        logger.exception("Auto-reply task error: %s", exc)
        raise self.retry(exc=exc, countdown=30)


# ─── Status Update Task ───────────────────────────────────────────────────────

@shared_task(
    bind=True,
    max_retries=2,
    name="whatsapp.update_status",
    queue="callbacks",
)
def update_message_status_task(self, provider_message_id: str, new_status: str):
    """
    Async task: update a message's delivery status from a provider callback.
    """
    from .services.message_service import MessageService

    try:
        service = MessageService()
        message = service.update_message_status(provider_message_id, new_status)
        if message:
            return {
                "status": "updated",
                "message_id": str(message.id),
                "new_status": new_status,
            }
        return {"status": "not_found", "provider_message_id": provider_message_id}

    except Exception as exc:
        logger.exception("Status update task error: %s", exc)
        raise self.retry(exc=exc, countdown=15)


# ─── Scheduled: Daily Stats Snapshot ─────────────────────────────────────────

@shared_task(
    name="whatsapp.daily_stats",
    queue="scheduled",
)
def capture_daily_stats():
    """
    Scheduled task: runs every day at midnight UTC.
    Logs a snapshot of system stats — extend to store in DB or push to analytics.
    """
    from .models import BusinessAccount, WhatsAppContact, Conversation, Message

    stats = {
        "timestamp": timezone.now().isoformat(),
        "business_accounts": BusinessAccount.objects.count(),
        "contacts": WhatsAppContact.objects.count(),
        "conversations_total": Conversation.objects.count(),
        "conversations_open": Conversation.objects.filter(status="open").count(),
        "messages_total": Message.objects.count(),
        "messages_today": Message.objects.filter(
            created_at__date=timezone.now().date()
        ).count(),
        "messages_inbound_today": Message.objects.filter(
            created_at__date=timezone.now().date(),
            direction="inbound",
        ).count(),
        "messages_outbound_today": Message.objects.filter(
            created_at__date=timezone.now().date(),
            direction="outbound",
        ).count(),
    }

    logger.info("Daily stats snapshot", extra=stats)
    return stats


# ─── Scheduled: Cleanup Old Task Results ─────────────────────────────────────

@shared_task(
    name="whatsapp.cleanup_task_results",
    queue="scheduled",
)
def cleanup_old_task_results():
    """
    Scheduled task: delete Celery task result records older than 7 days.
    Prevents django_celery_results table growing unbounded.
    """
    from django_celery_results.models import TaskResult
    cutoff = timezone.now() - timezone.timedelta(days=7)
    deleted, _ = TaskResult.objects.filter(date_done__lt=cutoff).delete()
    logger.info("Cleaned up %d old task results", deleted)
    return {"deleted": deleted}


# ─── Send Media Task ──────────────────────────────────────────────────────────

@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="whatsapp.send_media",
    queue="messages",
)
def send_whatsapp_media_task(
    self,
    business_id: str,
    to_number: str,
    media_url: str,
    media_type: str,
    caption: str = "",
    message_id: str = None,
):
    """
    Async task: send a media message via the WhatsApp provider.
    Retries up to 3 times with exponential backoff on failure.
    """
    from .models import Message, MediaAttachment, BusinessAccount
    from .services.whatsapp_client import get_whatsapp_client
    from .services.media_service import MediaService

    logger.info(
        "Media task started | type=%s | to=%s | attempt=%d",
        media_type, to_number, self.request.retries + 1,
    )

    try:
        business = BusinessAccount.objects.get(id=business_id, is_active=True)
        client   = get_whatsapp_client()

        result = client.send_media_message(
            to_number=to_number,
            from_number=business.phone_number_id,
            media_url=media_url,
            caption=caption,
            media_type=media_type,
        )

        if result.success and message_id:
            message = Message.objects.get(id=message_id)
            message.status               = Message.Status.SENT
            message.provider_message_id  = result.provider_message_id
            message.raw_payload          = result.raw_response
            message.status_updated_at    = timezone.now()
            message.save(update_fields=[
                "status", "provider_message_id",
                "raw_payload", "status_updated_at", "updated_at",
            ])
            message.conversation.update_last_message_time()

            # Update the MediaAttachment with provider's message ID
            MediaAttachment.objects.filter(message=message).update(
                provider_media_id=result.provider_message_id
            )

        if not result.success:
            raise Exception(result.error_message)

        logger.info(
            "Media task succeeded | sid=%s | to=%s",
            result.provider_message_id, to_number,
        )
        return {
            "status": "sent",
            "provider_message_id": result.provider_message_id,
            "message_id": message_id,
        }

    except Exception as exc:
        retry_delay = 60 * (2 ** self.request.retries)
        logger.warning(
            "Media task failed (attempt %d) | error=%s | retrying in %ds",
            self.request.retries + 1, exc, retry_delay,
        )
        raise self.retry(exc=exc, countdown=retry_delay)
    
# ─── Send Template Task ───────────────────────────────────────────────────────

@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="whatsapp.send_template",
    queue="messages",
)
def send_template_task(
    self,
    business_id: str,
    to_number: str,
    template_name: str,
    variables: list,
    language: str = "en",
):
    """
    Async task: send a WhatsApp template message.
    Used for both single sends and bulk send groups.
    Retries up to 3 times with exponential backoff.
    """
    from .services.template_service import TemplateService, TemplateError

    logger.info(
        "Template task started | template=%s | to=%s | attempt=%d",
        template_name, to_number, self.request.retries + 1,
    )

    try:
        svc = TemplateService()
        template_send = svc.send_template(
            business_id=business_id,
            to_number=to_number,
            template_name=template_name,
            variables=variables,
            language=language,
        )
        return {
            "status":           "sent",
            "template_send_id": str(template_send.id),
            "to_number":        to_number,
            "template_name":    template_name,
        }

    except TemplateError as exc:
        logger.warning(
            "Template task failed (attempt %d) | error=%s",
            self.request.retries + 1, exc,
        )
        retry_delay = 60 * (2 ** self.request.retries)
        raise self.retry(exc=exc, countdown=retry_delay)

    except Exception as exc:
        logger.exception("Unexpected template task error: %s", exc)
        raise self.retry(exc=exc, countdown=60)    
    
# ─── Webhook Delivery Task ────────────────────────────────────────────────────

@shared_task(
    bind=True,
    max_retries=3,
    name="whatsapp.deliver_webhook",
    queue="webhooks",
)
def deliver_webhook_task(
    self,
    endpoint_id: str,
    event_type: str,
    payload: dict,
    attempt: int = 1,
):
    """
    Deliver a single webhook event to one external endpoint.

    Retry schedule on failure:
      Attempt 1: immediate
      Attempt 2: 60 seconds
      Attempt 3: 300 seconds  (5 min)
      Attempt 4: 900 seconds  (15 min) → final, marks as FAILED
    """
    import requests
    from .models import WebhookEndpoint, WebhookDeliveryLog
    from django.utils import timezone

    RETRY_DELAYS = [60, 300, 900]

    try:
        endpoint = WebhookEndpoint.objects.get(id=endpoint_id)
    except WebhookEndpoint.DoesNotExist:
        logger.error("WebhookEndpoint not found: %s", endpoint_id)
        return {"status": "error", "reason": "endpoint_not_found"}

    # Create delivery log record
    log = WebhookDeliveryLog.objects.create(
        endpoint=endpoint,
        event_type=event_type,
        payload=payload,
        status=WebhookDeliveryLog.Status.PENDING,
        attempt_number=attempt,
    )

    # Build signature
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = _sign_payload(payload_bytes, endpoint.secret)

    headers = {
        "Content-Type":        "application/json",
        "X-Webhook-Signature": f"sha256={signature}",
        "X-Webhook-Event":     event_type,
        "X-Webhook-ID":        str(log.id),
        "X-Webhook-Attempt":   str(attempt),
        "User-Agent":          "WhatsAppAPI-Webhook/1.0",
    }

    start = time.monotonic()
    try:
        response = requests.post(
            endpoint.url,
            data=payload_bytes,
            headers=headers,
            timeout=10,     # 10 second timeout
        )
        duration_ms = int((time.monotonic() - start) * 1000)

        # 2xx = success
        if response.ok:
            log.status           = WebhookDeliveryLog.Status.SUCCESS
            log.http_status_code = response.status_code
            log.response_body    = response.text[:500]
            log.duration_ms      = duration_ms
            log.delivered_at     = timezone.now()
            log.save(update_fields=[
                "status", "http_status_code", "response_body",
                "duration_ms", "delivered_at", "updated_at",
            ])
            endpoint.increment_delivery(success=True)

            logger.info(
                "Webhook delivered | event=%s | url=%s | status=%d | %dms",
                event_type, endpoint.url[:60],
                response.status_code, duration_ms,
            )
            return {
                "status":      "delivered",
                "http_status": response.status_code,
                "duration_ms": duration_ms,
            }

        # Non-2xx — treat as failure and retry
        error_msg = (
            f"HTTP {response.status_code}: {response.text[:200]}"
        )
        raise Exception(error_msg)

    except Exception as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        error_str   = str(exc)
        retry_num   = self.request.retries

        logger.warning(
            "Webhook delivery failed | event=%s | url=%s | attempt=%d | error=%s",
            event_type, endpoint.url[:60], attempt, error_str,
        )

        if retry_num < self.max_retries:
            # Update log as retrying
            log.status        = WebhookDeliveryLog.Status.RETRYING
            log.error_message = error_str
            log.duration_ms   = duration_ms
            log.save(update_fields=[
                "status", "error_message", "duration_ms", "updated_at",
            ])
            retry_delay = RETRY_DELAYS[min(retry_num, len(RETRY_DELAYS) - 1)]
            raise self.retry(
                exc=exc,
                countdown=retry_delay,
                kwargs={
                    "endpoint_id": endpoint_id,
                    "event_type":  event_type,
                    "payload":     payload,
                    "attempt":     attempt + 1,
                },
            )

        # Final failure
        log.status           = WebhookDeliveryLog.Status.FAILED
        log.error_message    = error_str
        log.duration_ms      = duration_ms
        log.http_status_code = getattr(exc, "status_code", None)
        log.save(update_fields=[
            "status", "error_message", "duration_ms",
            "http_status_code", "updated_at",
        ])
        endpoint.increment_delivery(success=False)

        logger.error(
            "Webhook delivery permanently failed | event=%s | url=%s | error=%s",
            event_type, endpoint.url[:60], error_str,
        )
        return {"status": "failed", "error": error_str}


def _sign_payload(payload_bytes: bytes, secret: str) -> str:
    """HMAC-SHA256 signature of the raw payload bytes."""
    return hmac.new(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()    
    
    
# ─── API Key Cleanup Task ─────────────────────────────────────────────────────

@shared_task(
    name="whatsapp.cleanup_expired_keys",
    queue="scheduled",
)
def cleanup_expired_api_keys():
    """
    Scheduled task: auto-expire API keys past their expiry_at date.
    Run every hour via Celery Beat.
    """
    from .services.api_key_service import APIKeyService
    svc     = APIKeyService()
    expired = svc.cleanup_expired()
    logger.info("API key cleanup | expired=%d", expired)
    return {"expired_keys": expired}    