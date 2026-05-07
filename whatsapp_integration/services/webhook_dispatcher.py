import logging
from ..models import WebhookEndpoint

logger = logging.getLogger(__name__)


class WebhookDispatcher:
    """
    Dispatches an event to all active WebhookEndpoints that
    subscribe to that event type for the given business.

    Designed to be called fire-and-forget — it queues Celery
    tasks and returns immediately. Never blocks the caller.
    """

    def dispatch(
        self,
        business_id: str,
        event_type: str,
        payload: dict,
    ) -> int:
        """
        Find all matching endpoints and queue delivery tasks.
        Returns the number of tasks queued.
        """
        from ..tasks import deliver_webhook_task

        endpoints = WebhookEndpoint.objects.filter(
            business__id=business_id,
            is_active=True,
        )

        queued = 0
        for endpoint in endpoints:
            if not endpoint.subscribes_to(event_type):
                continue

            try:
                deliver_webhook_task.apply_async(
                    kwargs={
                        "endpoint_id": str(endpoint.id),
                        "event_type":  event_type,
                        "payload":     payload,
                        "attempt":     1,
                    },
                    queue="webhooks",
                )
                queued += 1
                logger.info(
                    "Webhook delivery queued | event=%s | endpoint=%s | url=%s",
                    event_type, endpoint.id, endpoint.url[:60],
                )
            except Exception as exc:
                logger.error(
                    "Failed to queue webhook delivery | "
                    "event=%s | endpoint=%s | error=%s",
                    event_type, endpoint.id, exc,
                )

        if queued == 0:
            logger.debug(
                "No active endpoints for event=%s | business=%s",
                event_type, business_id,
            )

        return queued