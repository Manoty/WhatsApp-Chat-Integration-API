import logging
from .language_detector import LanguageDetector, SUPPORTED_LANGUAGES
from ..models import Message, AutoReplyRule

logger = logging.getLogger(__name__)


class LanguageService:
    """
    Exposes language-related operations for the API layer:
      - Detect language of arbitrary text
      - Get language breakdown of messages
      - Validate rule language configs
    """

    def __init__(self):
        self.detector = LanguageDetector()

    def detect_text(self, text: str) -> dict:
        """
        Detect language of arbitrary text.
        Returns detection result as a dict.
        """
        result = self.detector.detect(text)
        return {
            "text":         text[:100],
            "language":     result.language,
            "language_name":self.detector.language_name(result.language),
            "confidence":   result.confidence,
            "is_reliable":  result.is_reliable,
            "is_supported": self.detector.is_supported(result.language),
        }

    def supported_languages(self) -> list[dict]:
        """Return list of all supported languages."""
        return [
            {"code": code, "name": name}
            for code, name in SUPPORTED_LANGUAGES.items()
        ]

    def message_language_breakdown(
        self, business_id: str, limit: int = 1000
    ) -> list[dict]:
        """
        Count messages by detected language for a business.
        Returns chart-ready breakdown.
        """
        from django.db.models import Count

        breakdown = (
            Message.objects.filter(
                conversation__business__id=business_id,
                direction="inbound",
            )
            .exclude(detected_language="")
            .values("detected_language")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        total = sum(r["count"] for r in breakdown)

        return [
            {
                "language":      r["detected_language"],
                "language_name": self.detector.language_name(
                    r["detected_language"]
                ),
                "count":         r["count"],
                "percentage":    round(r["count"] / total * 100, 1)
                                 if total else 0,
            }
            for r in breakdown
        ]

    def rule_language_coverage(self, business_id: str) -> dict:
        """
        Analyse which languages have auto-reply coverage.
        Useful for identifying gaps in rule configuration.
        """
        rules = AutoReplyRule.objects.filter(
            business__id=business_id,
            is_active=True,
        )

        # Languages with at least one rule
        covered_langs = set(
            rules.exclude(language="")
            .values_list("language", flat=True)
            .distinct()
        )

        has_neutral   = rules.filter(language="").exists()
        has_fallback  = rules.filter(is_fallback=True).exists()

        # Get languages seen in messages
        seen_langs = set(
            Message.objects.filter(
                conversation__business__id=business_id,
                direction="inbound",
            )
            .exclude(detected_language="")
            .values_list("detected_language", flat=True)
            .distinct()
        )

        uncovered = seen_langs - covered_langs

        return {
            "covered_languages":   list(covered_langs),
            "seen_in_messages":    list(seen_langs),
            "uncovered_languages": list(uncovered),
            "has_neutral_rules":   has_neutral,
            "has_fallback_rule":   has_fallback,
            "coverage_score":      round(
                (len(covered_langs) / len(seen_langs) * 100)
                if seen_langs else 100, 1
            ),
            "recommendations":     self._build_recommendations(
                uncovered, has_neutral, has_fallback
            ),
        }

    # ── Private ───────────────────────────────────────────────────────────────

    def _build_recommendations(
        self,
        uncovered: set,
        has_neutral: bool,
        has_fallback: bool,
    ) -> list[str]:
        recs = []

        for lang in uncovered:
            name = LanguageDetector().language_name(lang)
            recs.append(
                f"Add rules for {name} ({lang}) — "
                f"messages detected but no rules configured."
            )

        if not has_neutral:
            recs.append(
                "Consider adding language-neutral rules as a "
                "safety net for unsupported languages."
            )

        if not has_fallback:
            recs.append(
                "No fallback rule configured — "
                "unmatched messages will receive no reply."
            )

        return recs