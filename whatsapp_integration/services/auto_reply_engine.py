import re
import logging
from ..models import AutoReplyRule, Message, Conversation
from .message_service import MessageService, MessageSendError

logger = logging.getLogger(__name__)


class AutoReplyEngine:
    """
    Evaluates AutoReplyRules for a given business against an inbound message.

    Evaluation order:
      1. Load all active, non-fallback rules ordered by priority
      2. Test each rule's keyword against the message body
      3. On first match → send reply and stop
      4. If no match → check for a fallback rule
      5. If no fallback → do nothing (silent)

    Design principle: Fast, synchronous, and side-effect free except for
    triggering MessageService.send_message() on a match.
    """

    def __init__(self):
        self.message_service = MessageService()

    # ── Public entry point ────────────────────────────────────────────────────

    def process(self, message: Message) -> Message | None:
        """
        Evaluate rules against an inbound message.
        Returns the auto-reply Message if one was sent, else None.

        Only processes INBOUND messages — never auto-reply to outbound.
        """
        if message.direction != Message.Direction.INBOUND:
            return None

        conversation = message.conversation
        business = conversation.business
        body = message.body.strip()

        if not body:
            logger.debug("AutoReply skipped — empty message body")
            return None

        logger.debug(
            "AutoReply evaluating | business=%s | body='%s'",
            business.name, body[:60],
        )

        # ── Load rules ────────────────────────────────────────────────────────
        rules = AutoReplyRule.objects.filter(
            business=business,
            is_active=True,
            is_fallback=False,
        ).order_by("priority", "created_at")

        # ── Try each rule ─────────────────────────────────────────────────────
        for rule in rules:
            if self._matches(rule, body):
                logger.info(
                    "AutoReply rule matched | rule='%s' | keyword='%s' | body='%s'",
                    rule.name, rule.keyword, body[:60],
                )
                return self._send_reply(rule, conversation)

        # ── No match — try fallback ───────────────────────────────────────────
        fallback = AutoReplyRule.objects.filter(
            business=business,
            is_active=True,
            is_fallback=True,
        ).order_by("priority").first()

        if fallback:
            logger.info(
                "AutoReply fallback triggered | rule='%s'", fallback.name
            )
            return self._send_reply(fallback, conversation)

        logger.debug("AutoReply — no rule matched and no fallback configured")
        return None

    # ── Rule Matching ─────────────────────────────────────────────────────────

    def _matches(self, rule: AutoReplyRule, body: str) -> bool:
        """
        Test a single rule against the message body.
        All string comparisons are case-insensitive.
        """
        keyword = rule.keyword.strip()

        if not keyword:
            return False

        body_lower = body.lower()
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
                    "Invalid regex in rule '%s': %s", rule.name, exc
                )
                return False

        return False

    # ── Reply Sending ─────────────────────────────────────────────────────────

    def _send_reply(
        self, rule: AutoReplyRule, conversation: Conversation
    ) -> Message | None:
        """
        Send the rule's reply_text back to the contact.
        Increments the rule's trigger_count for analytics.
        """
        try:
            reply_message = self.message_service.send_message(
                business_id=str(conversation.business.id),
                to_number=conversation.contact.phone_number,
                body=rule.reply_text,
            )
            rule.increment_trigger_count()
            logger.info(
                "AutoReply sent | rule='%s' | to=%s | message_id=%s",
                rule.name,
                conversation.contact.phone_number,
                reply_message.id,
            )
            return reply_message

        except MessageSendError as exc:
            logger.error(
                "AutoReply send failed | rule='%s' | error=%s",
                rule.name, exc,
            )
            return None