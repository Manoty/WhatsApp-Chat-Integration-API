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
    WebhookEndpoint,     
    WebhookDeliveryLog,
    APIKey,
    Label,              
    ConversationLabel,  
    Agent,              
    AssignmentLog,
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
    
class WebhookEndpointSerializer(serializers.ModelSerializer):
    total_deliveries  = serializers.IntegerField(read_only=True)
    failed_deliveries = serializers.IntegerField(read_only=True)

    class Meta:
        model  = WebhookEndpoint
        fields = [
            "id", "business", "name", "url",
            "secret", "subscribed_events", "is_active",
            "total_deliveries", "failed_deliveries",
            "last_triggered_at", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "total_deliveries", "failed_deliveries",
            "last_triggered_at", "created_at", "updated_at",
        ]
        extra_kwargs = {
            # Never expose secret in list responses
            "secret": {"write_only": True},
        }

    def validate_url(self, value):
        if not value.startswith("https://"):
            raise serializers.ValidationError(
                "Webhook URL must use HTTPS."
            )
        return value

    def validate_subscribed_events(self, value):
        valid_events = [e.value for e in WebhookEndpoint.EventType] + ["*"]
        for event in value:
            if event not in valid_events:
                raise serializers.ValidationError(
                    f"Invalid event type: '{event}'. "
                    f"Valid options: {valid_events}"
                )
        if not value:
            raise serializers.ValidationError(
                "subscribed_events must contain at least one event type. "
                "Use [\"*\"] to subscribe to all events."
            )
        return value


class WebhookDeliveryLogSerializer(serializers.ModelSerializer):
    class Meta:
        model  = WebhookDeliveryLog
        fields = [
            "id", "endpoint", "event_type", "status",
            "http_status_code", "response_body", "error_message",
            "attempt_number", "duration_ms", "delivered_at",
            "created_at",
        ]
        read_only_fields = fields   
        
        
class APIKeySerializer(serializers.ModelSerializer):
    """
    Safe serializer — never exposes key_hash or raw key.
    The raw key is only returned by the create/rotate endpoints.
    """
    business_name = serializers.CharField(
        source="business.name", read_only=True
    )
    is_expired = serializers.SerializerMethodField()
    days_until_expiry = serializers.SerializerMethodField()

    class Meta:
        model  = APIKey
        fields = [
            "id", "business", "business_name", "name",
            "prefix", "scope", "status",
            "expiry_at", "last_used_at", "request_count",
            "allowed_ips", "rotated_from",
            "is_expired", "days_until_expiry",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "prefix", "status", "last_used_at",
            "request_count", "rotated_from",
            "is_expired", "days_until_expiry",
            "created_at", "updated_at",
        ]

    def get_is_expired(self, obj) -> bool:
        if obj.expiry_at:
            from django.utils import timezone
            return timezone.now() > obj.expiry_at
        return False

    def get_days_until_expiry(self, obj):
        if not obj.expiry_at:
            return None
        from django.utils import timezone
        delta = obj.expiry_at - timezone.now()
        return max(0, delta.days)


class CreateAPIKeySerializer(serializers.Serializer):
    """Validates POST /api/keys/"""
    business_id  = serializers.UUIDField()
    name         = serializers.CharField(max_length=255)
    scope        = serializers.ChoiceField(
        choices=APIKey.Scope.choices,
        default=APIKey.Scope.WRITE,
    )
    expiry_at    = serializers.DateTimeField(
        required=False,
        allow_null=True,
        help_text="ISO 8601 datetime. Null = never expires.",
    )
    allowed_ips  = serializers.ListField(
        child=serializers.IPAddressField(),
        default=list,
        required=False,
        help_text="Optional IP allowlist. Empty = all IPs allowed.",
    )

    def validate_expiry_at(self, value):
        if value:
            from django.utils import timezone
            if value <= timezone.now():
                raise serializers.ValidationError(
                    "expiry_at must be a future datetime."
                )
        return value

    def validate_name(self, value):
        if not value.strip():
            raise serializers.ValidationError("Name cannot be empty.")
        return value.strip()         
    
    
# ─── Label Serializers ────────────────────────────────────────────────────────

class LabelSerializer(serializers.ModelSerializer):
    conversation_count = serializers.SerializerMethodField()

    class Meta:
        model  = Label
        fields = [
            "id", "business", "name", "colour",
            "description", "is_active",
            "conversation_count", "created_at",
        ]
        read_only_fields = ["id", "created_at", "conversation_count"]

    def validate_name(self, value):
        value = value.strip().lower()
        if not value:
            raise serializers.ValidationError("Label name cannot be empty.")
        if len(value) > 50:
            raise serializers.ValidationError(
                "Label name cannot exceed 50 characters."
            )
        return value

    def get_conversation_count(self, obj) -> int:
        return obj.conversation_labels.count()


class ConversationLabelSerializer(serializers.ModelSerializer):
    label_name   = serializers.CharField(source="label.name",   read_only=True)
    label_colour = serializers.CharField(source="label.colour", read_only=True)

    class Meta:
        model  = ConversationLabel
        fields = [
            "id", "label", "label_name", "label_colour",
            "applied_by", "created_at",
        ]
        read_only_fields = fields


class ApplyLabelsSerializer(serializers.Serializer):
    """Validates PATCH /api/conversations/<id>/labels/"""
    labels = serializers.ListField(
        child=serializers.CharField(max_length=50),
        min_length=1,
        help_text="List of label names to apply",
    )


# ─── Agent Serializers ────────────────────────────────────────────────────────

class AgentSerializer(serializers.ModelSerializer):
    active_conversations = serializers.SerializerMethodField()
    is_available         = serializers.SerializerMethodField()
    capacity_pct         = serializers.SerializerMethodField()

    class Meta:
        model  = Agent
        fields = [
            "id", "business", "name", "email", "status",
            "max_conversations", "active_conversations",
            "is_available", "capacity_pct",
            "total_assigned", "total_resolved",
            "last_assigned_at", "created_at",
        ]
        read_only_fields = [
            "id", "active_conversations", "is_available",
            "capacity_pct", "total_assigned", "total_resolved",
            "last_assigned_at", "created_at",
        ]

    def get_active_conversations(self, obj) -> int:
        return obj.active_conversation_count

    def get_is_available(self, obj) -> bool:
        return obj.is_available

    def get_capacity_pct(self, obj) -> int:
        if not obj.max_conversations:
            return 0
        return round(
            (obj.active_conversation_count / obj.max_conversations) * 100
        )

    def validate_email(self, value):
        return value.strip().lower()

    def validate_max_conversations(self, value):
        if value < 1:
            raise serializers.ValidationError(
                "max_conversations must be at least 1."
            )
        if value > 200:
            raise serializers.ValidationError(
                "max_conversations cannot exceed 200."
            )
        return value


class AssignmentLogSerializer(serializers.ModelSerializer):
    agent_name  = serializers.CharField(source="agent.name",  read_only=True)
    agent_email = serializers.CharField(source="agent.email", read_only=True)

    class Meta:
        model  = AssignmentLog
        fields = [
            "id", "conversation", "agent", "agent_name",
            "agent_email", "assigned_by", "assignment_type",
            "unassigned_at", "unassignment_reason", "created_at",
        ]
        read_only_fields = fields


class ManualAssignSerializer(serializers.Serializer):
    """Validates POST /api/conversations/<id>/assign/"""
    agent_id    = serializers.UUIDField()
    assigned_by = serializers.CharField(
        max_length=255, required=False, default=""
    )    