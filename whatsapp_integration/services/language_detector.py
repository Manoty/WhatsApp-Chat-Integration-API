import logging
import re

logger = logging.getLogger(__name__)

# Minimum characters needed for reliable detection
MIN_DETECT_LENGTH = 8

# Languages we actively support with their ISO 639-1 codes
SUPPORTED_LANGUAGES = {
    "en": "English",
    "sw": "Swahili",
    "fr": "French",
    "ar": "Arabic",
    "es": "Spanish",
    "pt": "Portuguese",
    "de": "German",
    "zh": "Chinese",
    "hi": "Hindi",
}


class DetectionResult:
    """
    Result of a language detection attempt.
    """
    def __init__(
        self,
        language: str,
        confidence: float,
        is_reliable: bool,
    ):
        self.language    = language    # ISO 639-1 code e.g. "sw"
        self.confidence  = confidence  # 0.0 – 1.0
        self.is_reliable = is_reliable # False if text too short

    def __repr__(self):
        return (
            f"DetectionResult(language={self.language!r}, "
            f"confidence={self.confidence:.2f}, "
            f"reliable={self.is_reliable})"
        )


class LanguageDetector:
    """
    Detects the language of a text string using langdetect.

    Design decisions:
      - Returns "en" as default when detection fails or is unreliable
      - Short messages (< 8 chars) are marked unreliable
      - Emoji-only / number-only messages return empty string
      - Results are consistent via LANGDETECT_SEED setting
    """

    FALLBACK_LANGUAGE = "en"
    LANGDETECT_SEED   = 42          # makes langdetect deterministic

    def __init__(self):
        self._setup_langdetect()

    def _setup_langdetect(self):
        """Make langdetect deterministic across runs."""
        try:
            from langdetect import DetectorFactory
            DetectorFactory.seed = self.LANGDETECT_SEED
        except ImportError:
            logger.warning(
                "langdetect not installed — language detection disabled. "
                "Run: pip install langdetect"
            )

    def detect(self, text: str) -> DetectionResult:
        """
        Detect the language of a text string.
        Always returns a DetectionResult — never raises.
        """
        # Clean text
        cleaned = self._clean_text(text)

        # Too short or empty after cleaning
        if not cleaned or len(cleaned) < MIN_DETECT_LENGTH:
            logger.debug(
                "Text too short for reliable detection | len=%d | text=%r",
                len(cleaned), cleaned[:30],
            )
            return DetectionResult(
                language=self.FALLBACK_LANGUAGE,
                confidence=0.0,
                is_reliable=False,
            )

        try:
            from langdetect import detect_langs
            results = detect_langs(cleaned)

            if not results:
                return self._fallback()

            # Top result
            top    = results[0]
            lang   = top.lang
            conf   = round(float(top.prob), 3)

            # Map to closest supported language
            lang = self._normalize_language(lang)

            logger.debug(
                "Language detected | lang=%s | confidence=%.2f | text=%r",
                lang, conf, cleaned[:40],
            )

            return DetectionResult(
                language=lang,
                confidence=conf,
                is_reliable=conf >= 0.7,
            )

        except Exception as exc:
            logger.warning(
                "Language detection failed | error=%s | text=%r",
                exc, text[:40],
            )
            return self._fallback()

    def detect_batch(self, texts: list[str]) -> list[DetectionResult]:
        """Detect language for a list of texts."""
        return [self.detect(t) for t in texts]

    def is_supported(self, language_code: str) -> bool:
        """Check if a language code is in our supported set."""
        return language_code in SUPPORTED_LANGUAGES

    def language_name(self, code: str) -> str:
        """Return human-readable name for a language code."""
        return SUPPORTED_LANGUAGES.get(code, code.upper())

    # ── Private helpers ───────────────────────────────────────────────────────

    def _clean_text(self, text: str) -> str:
        """
        Strip noise that confuses language detection:
        - URLs
        - Phone numbers
        - Pure emoji sequences
        - Excessive whitespace
        """
        # Remove URLs
        text = re.sub(r"https?://\S+", "", text)
        # Remove phone numbers
        text = re.sub(r"\+?\d[\d\s\-]{7,}\d", "", text)
        # Remove emoji (basic range)
        text = re.sub(
            r"[\U0001F600-\U0001F64F"
            r"\U0001F300-\U0001F5FF"
            r"\U0001F680-\U0001F6FF"
            r"\U0001F700-\U0001F77F"
            r"\U0001F780-\U0001F7FF"
            r"\U0001F800-\U0001F8FF"
            r"\U0001F900-\U0001F9FF"
            r"\U0001FA00-\U0001FA6F"
            r"\U00002700-\U000027BF"
            r"\U000024C2-\U0001F251]+",
            " ",
            text,
        )
        return text.strip()

    def _normalize_language(self, lang: str) -> str:
        """
        Normalize langdetect output to our supported codes.
        e.g. 'zh-cn' → 'zh', 'pt-br' → 'pt'
        """
        # Strip region subtag
        base = lang.split("-")[0].lower()
        # Return as-is if supported, else fallback
        return base if base in SUPPORTED_LANGUAGES else self.FALLBACK_LANGUAGE

    def _fallback(self) -> DetectionResult:
        return DetectionResult(
            language=self.FALLBACK_LANGUAGE,
            confidence=0.0,
            is_reliable=False,
        )