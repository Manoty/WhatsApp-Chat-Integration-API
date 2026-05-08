import re
import logging
from ..models import AutoReplyRule, Message, Conversation
from .message_service import MessageService, MessageSendError
from .language_detector import LanguageDetector

logger = logging.getLogger(__name__)


class AutoReplyEngine:
    """
    Evaluates AutoReplyRules against an inbound message
    with full multi-language support.

    Matching passes (in order):
      1. Language-specific rules matching detected language
      2. Language-neutral rules (language = "")
      3. Language-specific fallback for detected language
      4. Language-neutral fallback
      5. Silence (no reply)

    This ensures contacts always get a reply in their language
    when a matching rule exists, with graceful fallback.
    """

    def __init__(self):
        self.message_service = MessageService()
        self.detector        = LanguageDetector()

    # ── Public entry point ────────────────────────────────────────────────────

    def process(self, message: Message) -> Message | None:
        """
        Evaluate rules against an inbound message.
        Returns the auto-reply Message if sent, else None.
        """
        if message.direction != Message.Direction.INBOUND:
            return None

        conversation = message.conversation
        business     = conversation.business
        body         = message.body.strip()

        if not body:
            logger.debug("AutoReply skipped — empty body")
            return None

        # Use stored detected language or detect now
        detected_lang = message.detected_language
        if not detected_lang:
            result        = self.detector.detect(body)
            detected_lang = result.language

        logger.debug(
            "AutoReply evaluating | business=%s | lang=%s | body=%r",
            business.name, detected_lang, body[:60],
        )

        # Load all active non-fallback rules ordered by priority
        rules = AutoReplyRule.objects.filter(
            business=business,
            is_active=True,
            is_fallback=False,
        ).order_by("priority", "created_at")

        # ── Pass 1: Language-specific keyword match ───────────────────────────
        matched = self._match_rules(
            rules.filter(language=detected_lang), body
        )
        if matched:
            logger.info(
                "AutoReply matched (lang-specific) | rule=%s | lang=%s",
                matched.name, detected_lang,
            )
            return self._send_reply(matched, conversation)

        # ── Pass 2: Language-neutral keyword match ────────────────────────────
        matched = self._match_rules(
            rules.filter(language=""), body
        )
        if matched:
            logger.info(
                "AutoReply matched (lang-neutral) | rule=%s", matched.name
            )
            return self._send_reply(matched, conversation)

        # ── Pass 3: Language-specific fallback ───────────────────────────────
        fallback = AutoReplyRule.objects.filter(
            business=business,
            is_active=True,
            is_fallback=True,
            language=detected_lang,
        ).order_by("priority").first()

        if fallback:
            logger.info(
                "AutoReply fallback (lang-specific) | rule=%s | lang=%s",
                fallback.name, detected_lang,
            )
            return self._send_reply(fallback, conversation)

        # ── Pass 4: Language-neutral fallback ─────────────────────────────────
        fallback = AutoReplyRule.objects.filter(
            business=business,
            is_active=True,
            is_fallback=True,
            language="",
        ).order_by("priority").first()

        if fallback:
            logger.info(
                "AutoReply fallback (lang-neutral) | rule=%s", fallback.name
            )
            return self._send_reply(fallback, conversation)

        logger.debug(
            "AutoReply — no match and no fallback | lang=%s", detected_lang
        )
        return None

    # ── Rule Matching ─────────────────────────────────────────────────────────

    def _match_rules(
        self, rules_qs, body: str
    ) -> AutoReplyRule | None:
        """Iterate rules and return first match."""
        for rule in rules_qs:
            if self._matches(rule, body):
                return rule
        return None

    def _matches(self, rule: AutoReplyRule, body: str) -> bool:
        """
        Test a single rule against the message body.
        Case-insensitive for all string comparisons.
        """
        keyword = rule.keyword.strip()
        if not keyword:
            return False

        body_lower    = body.lower()
        keyword_lower = keyword.lower()

        if rule.match_type == AutoReplyRule.MatchType.EXACT:
            return body_lower == keyword_lower

        if rule.match_type == AutoReplyRule.MatchType.CONTAINS:
            return keyword_lower in body_lower

        if rule.match_type == AutoReplyRule.MatchType.STARTSWITH:
            return body_lower.startswith(keyword_lower)

        if rule.match_type == AutoReplyRule.MatchType.REGEX:
            try:
                return bool(re.search(keyword, body, re.IGNORECASE))
            except re.error as exc:
                logger.warning(
                    "Invalid regex in rule %r: %s", rule.name, exc
                )
                return False

        return False

    # ── Reply Sending ─────────────────────────────────────────────────────────

    def _send_reply(
        self, rule: AutoReplyRule, conversation: Conversation
    ) -> Message | None:
        """Send the rule's reply_text and increment trigger count."""
        try:
            reply = self.message_service.send_message(
                business_id=str(conversation.business.id),
                to_number=conversation.contact.phone_number,
                body=rule.reply_text,
            )
            rule.increment_trigger_count()
            logger.info(
                "AutoReply sent | rule=%s | to=%s | msg=%s",
                rule.name,
                conversation.contact.phone_number,
                reply.id,
            )
            return reply
        except MessageSendError as exc:
            logger.error(
                "AutoReply send failed | rule=%s | error=%s",
                rule.name, exc,
            )
            return None