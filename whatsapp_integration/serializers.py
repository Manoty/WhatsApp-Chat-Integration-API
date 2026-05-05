from rest_framework import serializers
from .models import BusinessAccount, WhatsAppContact, Conversation, Message


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


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = [
            "id", "conversation", "direction", "message_type",
            "body", "provider_message_id", "status",
            "status_updated_at", "created_at",
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