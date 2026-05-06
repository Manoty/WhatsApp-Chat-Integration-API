import re
from rest_framework import serializers
from .models import (
    BusinessAccount,
    WhatsAppContact,
    Conversation,
    Message,
    AutoReplyRule,
    MediaAttachment,
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

