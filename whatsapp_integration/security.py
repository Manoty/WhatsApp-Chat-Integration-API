import hmac
import hashlib
import base64
import logging
from django.conf import settings

logger = logging.getLogger(__name__)


class TwilioSignatureVerifier:
    """
    Verifies that a webhook POST came from Twilio and not a third party.
    Uses HMAC-SHA1 of (URL + sorted POST params) signed with AUTH_TOKEN.

    Docs: https://www.twilio.com/docs/usage/webhooks/webhooks-security
    Enable with TWILIO_WEBHOOK_VALIDATE=True in production.
    """

    def verify(self, request) -> bool:
        if not getattr(settings, "TWILIO_WEBHOOK_VALIDATE", False):
            logger.debug("Twilio signature validation disabled — skipping")
            return True

        auth_token = settings.TWILIO_AUTH_TOKEN
        if not auth_token:
            logger.warning("TWILIO_AUTH_TOKEN not set — skipping signature check")
            return True

        twilio_signature = request.headers.get("X-Twilio-Signature", "")
        if not twilio_signature:
            logger.warning("Missing X-Twilio-Signature header")
            return False

        url = request.build_absolute_uri()
        post_data = request.POST  # Twilio sends form-encoded

        # Build the validation string: URL + sorted key=value pairs
        params_string = "".join(
            f"{k}{v}" for k, v in sorted(post_data.items())
        )
        validation_string = url + params_string

        # HMAC-SHA1
        mac = hmac.new(
            auth_token.encode("utf-8"),
            validation_string.encode("utf-8"),
            hashlib.sha1,
        )
        computed = base64.b64encode(mac.digest()).decode("utf-8")
        valid = hmac.compare_digest(computed, twilio_signature)

        if not valid:
            logger.warning(
                "Twilio signature mismatch",
                extra={"url": url, "signature": twilio_signature[:20]},
            )
        return valid


class MetaSignatureVerifier:
    """
    Verifies Meta (WhatsApp Business API) webhook payloads.
    Uses HMAC-SHA256 of raw request body signed with APP_SECRET.

    Docs: https://developers.facebook.com/docs/messenger-platform/webhooks#security
    Enable by setting META_APP_SECRET in environment.
    """

    def verify(self, request) -> bool:
        app_secret = getattr(settings, "META_APP_SECRET", "")
        if not app_secret:
            logger.debug("META_APP_SECRET not set — skipping Meta signature check")
            return True

        signature_header = request.headers.get("X-Hub-Signature-256", "")
        if not signature_header or not signature_header.startswith("sha256="):
            logger.warning("Missing or malformed X-Hub-Signature-256 header")
            return False

        received_signature = signature_header[len("sha256="):]
        raw_body = request.body

        mac = hmac.new(
            app_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        )
        computed = mac.hexdigest()
        valid = hmac.compare_digest(computed, received_signature)

        if not valid:
            logger.warning(
                "Meta signature mismatch",
                extra={"signature": received_signature[:20]},
            )
        return valid


def verify_webhook_signature(request, source: str) -> bool:
    """
    Factory — routes to the correct verifier based on source.
    Call this at the top of the webhook view.
    """
    if source == "twilio":
        return TwilioSignatureVerifier().verify(request)
    if source == "meta":
        return MetaSignatureVerifier().verify(request)
    return True