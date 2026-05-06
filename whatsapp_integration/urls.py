from django.urls import path
from . import views

app_name = "whatsapp_integration"

urlpatterns = [
    # ── System ────────────────────────────────────────────────────────────────
    path("health/",  views.health_check,  name="health-check"),
    path("stats/",   views.system_stats,  name="system-stats"),

    # ── Webhook ───────────────────────────────────────────────────────────────
    path("webhook/whatsapp/", views.webhook_receiver, name="webhook-receiver"),

    # ── Messaging ─────────────────────────────────────────────────────────────
    path("messages/send/",                    views.send_message,           name="send-message"),
    path("messages/send/async/",              views.send_message_async,     name="send-message-async"),
    path("messages/send/media/",              views.send_media_message,     name="send-media-message"),
    path("messages/status/",                  views.message_status_callback,name="message-status-callback"),
    path("messages/<uuid:message_id>/media/", views.message_media,          name="message-media"),

    # ── Task Tracking ─────────────────────────────────────────────────────────
    path("tasks/<str:task_id>/", views.task_status, name="task-status"),

    # ── Conversations ─────────────────────────────────────────────────────────
    path("conversations/",                                 views.conversation_list,     name="conversation-list"),
    path("conversations/<uuid:conversation_id>/",          views.conversation_detail,   name="conversation-detail"),
    path("conversations/<uuid:conversation_id>/messages/", views.conversation_messages, name="conversation-messages"),
    path("conversations/<uuid:conversation_id>/media/",    views.conversation_media,    name="conversation-media"),

    # ── Contacts ──────────────────────────────────────────────────────────────
    path("contacts/",                   views.contact_list,   name="contact-list"),
    path("contacts/<uuid:contact_id>/", views.contact_detail, name="contact-detail"),

    # ── Auto Reply Rules ──────────────────────────────────────────────────────
    path("auto-replies/",                views.auto_reply_rule_list,   name="auto-reply-list"),
    path("auto-replies/test/",           views.test_auto_reply,        name="auto-reply-test"),
    path("auto-replies/<uuid:rule_id>/", views.auto_reply_rule_detail, name="auto-reply-detail"),

    # ── Templates ─────────────────────────────────────────────────────────────
    path("templates/",                            views.template_list,         name="template-list"),
    path("templates/send/",                       views.template_send,         name="template-send"),
    path("templates/send/bulk/",                  views.template_bulk_send,    name="template-bulk-send"),
    path("templates/preview/",                    views.template_preview,      name="template-preview"),
    path("templates/<uuid:template_id>/",         views.template_detail,       name="template-detail"),
    path("templates/<uuid:template_id>/submit/",  views.template_submit,       name="template-submit"),
    path("templates/<uuid:template_id>/history/", views.template_send_history, name="template-send-history"),
]