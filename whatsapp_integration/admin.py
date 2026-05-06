from django.contrib import admin
from .models import BusinessAccount, WhatsAppContact, Conversation, Message
 
from .models import AutoReplyRule
from .models import MediaAttachment

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