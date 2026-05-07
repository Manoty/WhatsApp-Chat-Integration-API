from django.contrib import admin
from .models import BusinessAccount, WhatsAppContact, Conversation, Message
 
from .models import AutoReplyRule
from .models import MediaAttachment
from .models import MessageTemplate, TemplateSend

from .models import WebhookEndpoint, WebhookDeliveryLog



@admin.register(BusinessAccount)
class BusinessAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "phone_number_id", "is_active", "created_at")
    search_fields = ("name", "phone_number_id")
    list_filter = ("is_active",)


@admin.register(WhatsAppContact)
class WhatsAppContactAdmin(admin.ModelAdmin):
    list_display = ("phone_number", "display_name", "business", "is_opted_in", "last_seen")
    search_fields = ("phone_number", "display_name")
    list_filter = ("business", "is_opted_in")


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "contact", "business", "status", "last_message_at")
    search_fields = ("contact__phone_number", "business__name")
    list_filter = ("status", "business")


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("id", "conversation", "direction", "message_type", "status", "created_at")
    search_fields = ("body", "provider_message_id")
    list_filter = ("direction", "status", "message_type")
    readonly_fields = ("raw_payload",)
    
   

@admin.register(AutoReplyRule)
class AutoReplyRuleAdmin(admin.ModelAdmin):
    list_display = (
        "name", "business", "match_type", "keyword",
        "is_active", "is_fallback", "priority", "trigger_count",
    )
    list_filter = ("business", "is_active", "is_fallback", "match_type")
    search_fields = ("name", "keyword", "reply_text")
    readonly_fields = ("trigger_count", "created_at", "updated_at")
    ordering = ("business", "priority")    
    
@admin.register(MediaAttachment)
class MediaAttachmentAdmin(admin.ModelAdmin):
    list_display  = ("id", "category", "mime_type", "file_name",
                     "file_size", "is_downloaded", "created_at")
    list_filter   = ("category", "is_downloaded")
    search_fields = ("file_name", "mime_type", "provider_media_id")
    readonly_fields = ("created_at", "updated_at")  
    

@admin.register(MessageTemplate)
class MessageTemplateAdmin(admin.ModelAdmin):
    list_display  = (
        "name", "template_name", "business", "category",
        "language", "status", "variable_count",
        "send_count", "success_count", "created_at",
    )
    list_filter   = ("status", "category", "language", "business")
    search_fields = ("name", "template_name", "body")
    readonly_fields = (
        "variable_count", "send_count", "success_count",
        "provider_template_id", "created_at", "updated_at",
    )
    actions = ["submit_for_approval"]

    def submit_for_approval(self, request, queryset):
        from .services.template_service import TemplateService
        svc = TemplateService()
        for template in queryset.filter(
            status__in=["draft", "rejected"]
        ):
            svc.submit_for_approval(template)
        self.message_user(request, "Selected templates submitted for approval.")
    submit_for_approval.short_description = "Submit selected templates for approval"


@admin.register(TemplateSend)
class TemplateSendAdmin(admin.ModelAdmin):
    list_display  = (
        "id", "template", "contact", "status",
        "sent_at", "created_at",
    )
    list_filter   = ("status", "template")
    search_fields = ("contact__phone_number", "rendered_body")
    readonly_fields = (
        "rendered_body", "variables", "provider_message_id",
        "sent_at", "created_at",
    )      
    
    

@admin.register(WebhookEndpoint)
class WebhookEndpointAdmin(admin.ModelAdmin):
    list_display  = (
        "name", "business", "url", "is_active",
        "total_deliveries", "failed_deliveries", "last_triggered_at",
    )
    list_filter   = ("is_active", "business")
    search_fields = ("name", "url")
    readonly_fields = (
        "total_deliveries", "failed_deliveries",
        "last_triggered_at", "created_at", "updated_at",
    )


@admin.register(WebhookDeliveryLog)
class WebhookDeliveryLogAdmin(admin.ModelAdmin):
    list_display  = (
        "event_type", "endpoint", "status",
        "http_status_code", "attempt_number",
        "duration_ms", "delivered_at",
    )
    list_filter   = ("status", "event_type")
    search_fields = ("event_type", "error_message")
    readonly_fields = (
        "payload", "response_body", "error_message",
        "delivered_at", "created_at",
    )    