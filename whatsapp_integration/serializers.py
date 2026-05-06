import re
from rest_framework import serializers
from .models import (
    BusinessAccount,
    WhatsAppContact,
    Conversation,
    Message,
    AutoReplyRule,
    MediaAttachment,
    MessageTemplate,
    TemplateSend,
)

class BusinessAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = BusinessAccount
        fields = [
            "id", "name", "phone_number_id",
            "is_active", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class WhatsAppContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = WhatsAppContact
        fields = [
            "id", "business", "phone_number", "display_name",
            "is_opted_in", "last_seen", "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class MediaAttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = MediaAttachment
        fields = [
            "id", "category", "media_url", "provider_media_id",
            "mime_type", "file_name", "file_size", "caption",
            "stored_url", "is_downloaded", "created_at",
        ]
        read_only_fields = fields

class MessageSerializer(serializers.ModelSerializer):
    media_attachment = MediaAttachmentSerializer(read_only=True)

    class Meta:
        model = Message
        fields = [
            "id", "conversation", "direction", "message_type",
            "body", "provider_message_id", "status",
            "status_updated_at", "created_at",
            "media_attachment",        # ← NEW
        ]
        read_only_fields = ["id", "created_at", "status_updated_at"]

class ConversationSerializer(serializers.ModelSerializer):
    contact = WhatsAppContactSerializer(read_only=True)
    last_message = serializers.SerializerMethodField()
    message_count = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            "id", "business", "contact", "status",
            "last_message_at", "assigned_to",
            "message_count", "last_message",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_last_message(self, obj):
        msg = obj.messages.order_by("-created_at").first()
        if msg:
            return MessageSerializer(msg).data
        return None

    def get_message_count(self, obj):
        return obj.messages.count()
    
class SendMessageRequestSerializer(serializers.Serializer):
    """Validates the request body for POST /api/messages/send/"""
    business_id = serializers.UUIDField(
        help_text="UUID of the BusinessAccount sending the message"
    )
    to_number = serializers.CharField(
        max_length=20,
        help_text="Recipient phone number in E.164 format e.g. +254712345678"
    )
    body = serializers.CharField(
        max_length=4096,
        help_text="Message text content"
    )
    message_type = serializers.ChoiceField(
        choices=["text"],        # Expand in later phases
        default="text",
        required=False,
    )

    def validate_to_number(self, value):
        value = value.strip()
        if not value.startswith("+"):
            value = f"+{value}"
        if len(value) < 8:
            raise serializers.ValidationError(
                "Phone number too short. Use E.164 format e.g. +254712345678"
            )
        return value

    def validate_body(self, value):
        if not value.strip():
            raise serializers.ValidationError("Message body cannot be empty.")
        return value.strip()


class SendMessageResponseSerializer(serializers.Serializer):
    """Shape of the response returned after a successful send."""
    message_id = serializers.UUIDField()
    conversation_id = serializers.UUIDField()
    status = serializers.CharField()
    provider_message_id = serializers.CharField()
    to_number = serializers.CharField()
    body = serializers.CharField()
    created_at = serializers.DateTimeField()    
    
class AutoReplyRuleSerializer(serializers.ModelSerializer):
    trigger_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = AutoReplyRule
        fields = [
            "id", "business", "name", "keyword", "match_type",
            "reply_text", "is_active", "is_fallback", "priority",
            "trigger_count", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "trigger_count", "created_at", "updated_at"]

    def validate(self, data):
        # Fallback rules don't need a keyword
        is_fallback = data.get("is_fallback", False)
        keyword = data.get("keyword", "").strip()

        if not is_fallback and not keyword:
            raise serializers.ValidationError(
                {"keyword": "Keyword is required for non-fallback rules."}
            )

        # Validate regex pattern if match_type is regex
        match_type = data.get("match_type", AutoReplyRule.MatchType.CONTAINS)
        if match_type == AutoReplyRule.MatchType.REGEX and keyword:
            try:
                re.compile(keyword)
            except re.error as exc:
                raise serializers.ValidationError(
                    {"keyword": f"Invalid regular expression: {exc}"}
                )

        return data



class SendMediaRequestSerializer(serializers.Serializer):
    """Validates POST /api/messages/send/media/"""
    business_id = serializers.UUIDField()
    to_number   = serializers.CharField(max_length=20)
    media_url   = serializers.URLField(
        help_text="Publicly accessible URL of the media file"
    )
    media_type  = serializers.ChoiceField(
        choices=["image", "audio", "video", "document"],
        default="image",
    )
    caption     = serializers.CharField(
        max_length=1024,
        required=False,
        default="",
        allow_blank=True,
    )

    def validate_to_number(self, value):
        value = value.strip()
        if not value.startswith("+"):
            value = f"+{value}"
        if len(value) < 8:
            raise serializers.ValidationError(
                "Phone number too short. Use E.164 format."
            )
        return value

    def validate_media_url(self, value):
        if not value.startswith("https://"):
            raise serializers.ValidationError(
                "media_url must be a publicly accessible HTTPS URL."
            )
        return value


class MessageTemplateSerializer(serializers.ModelSerializer):
    variable_count = serializers.IntegerField(read_only=True)
    send_count     = serializers.IntegerField(read_only=True)
    success_count  = serializers.IntegerField(read_only=True)

    class Meta:
        model  = MessageTemplate
        fields = [
            "id", "business", "name", "template_name",
            "category", "language", "body",
            "header_text", "header_media_url", "footer_text",
            "variable_count", "status", "provider_template_id",
            "rejection_reason", "send_count", "success_count",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "variable_count", "status",
            "provider_template_id", "rejection_reason",
            "send_count", "success_count",
            "created_at", "updated_at",
        ]

    def validate_template_name(self, value):
        """Enforce snake_case — Meta requirement."""
        import re
        value = value.strip().lower().replace(" ", "_")
        if not re.match(r"^[a-z0-9_]+$", value):
            raise serializers.ValidationError(
                "template_name must be snake_case "
                "(lowercase letters, numbers, underscores only)."
            )
        return value

    def validate_body(self, value):
        if not value.strip():
            raise serializers.ValidationError("Template body cannot be empty.")
        return value.strip()


class TemplateSendSerializer(serializers.ModelSerializer):
    template_name = serializers.CharField(
        source="template.template_name", read_only=True
    )
    contact_phone = serializers.CharField(
        source="contact.phone_number", read_only=True
    )

    class Meta:
        model  = TemplateSend
        fields = [
            "id", "template", "template_name", "contact",
            "contact_phone", "variables", "rendered_body",
            "status", "provider_message_id", "error_message",
            "sent_at", "created_at",
        ]
        read_only_fields = fields


class SendTemplateRequestSerializer(serializers.Serializer):
    """Validates POST /api/templates/send/"""
    business_id   = serializers.UUIDField()
    to_number     = serializers.CharField(max_length=20)
    template_name = serializers.CharField(max_length=512)
    variables     = serializers.ListField(
        child=serializers.CharField(max_length=1024),
        default=list,
        help_text="Ordered list of values for {{1}}, {{2}}, {{3}} ...",
    )
    language      = serializers.CharField(max_length=10, default="en")

    def validate_to_number(self, value):
        value = value.strip()
        if not value.startswith("+"):
            value = f"+{value}"
        if len(value) < 8:
            raise serializers.ValidationError(
                "Phone number too short. Use E.164 format."
            )
        return value


class BulkSendTemplateRequestSerializer(serializers.Serializer):
    """Validates POST /api/templates/send/bulk/"""

    class RecipientSerializer(serializers.Serializer):
        to_number = serializers.CharField(max_length=20)
        variables = serializers.ListField(
            child=serializers.CharField(max_length=1024),
            default=list,
        )

    business_id   = serializers.UUIDField()
    template_name = serializers.CharField(max_length=512)
    language      = serializers.CharField(max_length=10, default="en")
    recipients    = RecipientSerializer(many=True, min_length=1)

    def validate_recipients(self, value):
        if len(value) > 1000:
            raise serializers.ValidationError(
                "Maximum 1,000 recipients per bulk send request."
            )
        return value