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