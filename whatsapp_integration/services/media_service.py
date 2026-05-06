import logging
from django.utils import timezone
from ..models import Message, MediaAttachment, Conversation, BusinessAccount

logger = logging.getLogger(__name__)


class MediaService:
    """
    Handles all media-related operations:
      - Parsing inbound media from Twilio/Meta webhook payloads
      - Creating MediaAttachment records
      - Building outbound media send payloads
      - Downloading media metadata (future: S3 upload)
    """

    # ── Inbound: Extract from Twilio payload ──────────────────────────────────

    def extract_twilio_media(self, payload: dict) -> list[dict]:
        """
        Twilio attaches media as numbered fields:
          MediaUrl0, MediaContentType0
          MediaUrl1, MediaContentType1  (for multiple files)

        Returns a list of media dicts.
        """
        num_media = int(payload.get("NumMedia", 0))
        media_items = []

        for i in range(num_media):
            url  = payload.get(f"MediaUrl{i}", "")
            mime = payload.get(f"MediaContentType{i}", "")

            if url:
                media_items.append({
                    "media_url":        url,
                    "mime_type":        mime,
                    "category":         self._mime_to_category(mime),
                    "provider_media_id": "",
                    "file_name":        self._extract_filename(url, mime),
                    "caption":          payload.get("Body", ""),
                })

        return media_items

    # ── Inbound: Extract from Meta payload ───────────────────────────────────

    def extract_meta_media(self, message_data: dict) -> list[dict]:
        """
        Meta embeds media under a type-specific key:
          { "type": "image", "image": {"id": "...", "mime_type": "image/jpeg"} }

        Returns a list of media dicts (usually just one per message).
        """
        msg_type = message_data.get("type", "text")

        if msg_type not in ("image", "audio", "video", "document", "sticker"):
            return []

        media_data = message_data.get(msg_type, {})
        if not media_data:
            return []

        return [{
            "media_url":         media_data.get("link", ""),
            "mime_type":         media_data.get("mime_type", ""),
            "category":          msg_type,
            "provider_media_id": media_data.get("id", ""),
            "file_name":         media_data.get("filename", ""),
            "caption":           message_data.get("caption", ""),
            "file_size":         media_data.get("file_size"),
        }]

    # ── Store MediaAttachment record ──────────────────────────────────────────

    def create_attachment(self, message: Message, media_dict: dict) -> MediaAttachment:
        """
        Persist a MediaAttachment linked to an existing Message.
        """
        attachment = MediaAttachment.objects.create(
            message=message,
            category=media_dict.get("category", MediaAttachment.MediaCategory.IMAGE),
            media_url=media_dict.get("media_url", ""),
            provider_media_id=media_dict.get("provider_media_id", ""),
            mime_type=media_dict.get("mime_type", ""),
            file_name=media_dict.get("file_name", ""),
            file_size=media_dict.get("file_size"),
            caption=media_dict.get("caption", ""),
        )

        logger.info(
            "MediaAttachment created | id=%s | category=%s | message=%s",
            attachment.id, attachment.category, message.id,
        )
        return attachment

    # ── Outbound: Build provider payload ─────────────────────────────────────

    def build_twilio_media_payload(
        self,
        to_number: str,
        from_number: str,
        media_url: str,
        caption: str = "",
    ) -> dict:
        """Build Twilio API payload for sending media."""
        return {
            "from_": f"whatsapp:{from_number}",
            "to":    f"whatsapp:{to_number}",
            "media_url": [media_url],
            "body": caption,
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _mime_to_category(self, mime_type: str) -> str:
        mime = mime_type.lower()
        if mime.startswith("image/"):
            return MediaAttachment.MediaCategory.IMAGE
        if mime.startswith("audio/"):
            return MediaAttachment.MediaCategory.AUDIO
        if mime.startswith("video/"):
            return MediaAttachment.MediaCategory.VIDEO
        if mime.startswith("application/") or mime.startswith("text/"):
            return MediaAttachment.MediaCategory.DOCUMENT
        return MediaAttachment.MediaCategory.IMAGE

    def _extract_filename(self, url: str, mime_type: str) -> str:
        """Best-effort filename from URL or mime type."""
        MIME_EXTENSIONS = {
            "image/jpeg":       "image.jpg",
            "image/png":        "image.png",
            "image/webp":       "image.webp",
            "audio/ogg":        "audio.ogg",
            "audio/mpeg":       "audio.mp3",
            "video/mp4":        "video.mp4",
            "application/pdf":  "document.pdf",
        }
        try:
            from urllib.parse import urlparse
            path = urlparse(url).path
            name = path.split("/")[-1]
            if "." in name:
                return name
        except Exception:
            pass
        return MIME_EXTENSIONS.get(mime_type.lower(), "file")