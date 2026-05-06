import logging
from celery import shared_task
from celery.utils.log import get_task_logger
from django.utils import timezone

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