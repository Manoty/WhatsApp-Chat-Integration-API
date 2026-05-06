from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class WebhookRateThrottle(AnonRateThrottle):
    """
    Generous limit for webhook endpoints — WhatsApp can burst.
    120 requests/minute per IP.
    """
    scope = "webhook"


class SendMessageRateThrottle(UserRateThrottle):
    """
    Tighter limit for the send endpoint — prevents accidental spam.
    60 messages/minute per authenticated key.
    """
    scope = "send_message"