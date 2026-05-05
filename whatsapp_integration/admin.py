from django.contrib import admin
from .models import BusinessAccount, WhatsAppContact, Conversation, Message


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