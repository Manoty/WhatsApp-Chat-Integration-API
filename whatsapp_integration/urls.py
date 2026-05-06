from django.urls import path
from . import views

app_name = "whatsapp_integration"

urlpatterns = [
    # ── System ────────────────────────────────────────────────────────────────
    path("health/", views.health_check, name="health-check"),
    path("stats/", views.system_stats, name="system-stats"),

    # ── Webhook ───────────────────────────────────────────────────────────────
    path("webhook/whatsapp/", views.webhook_receiver, name="webhook-receiver"),

    # ── Messaging (sync + async) ──────────────────────────────────────────────
    path("messages/send/", views.send_message, name="send-message"),
    path("messages/send/async/", views.send_message_async, name="send-message-async"),
    path("messages/status/", views.message_status_callback, name="message-status-callback"),

    # ── Task Tracking ─────────────────────────────────────────────────────────
    path("tasks/<str:task_id>/", views.task_status, name="task-status"),

    # ── Conversations ─────────────────────────────────────────────────────────
    path("conversations/", views.conversation_list, name="conversation-list"),
    path("conversations/<uuid:conversation_id>/", views.conversation_detail, name="conversation-detail"),
    path("conversations/<uuid:conversation_id>/messages/", views.conversation_messages, name="conversation-messages"),

    # ── Contacts ──────────────────────────────────────────────────────────────
    path("contacts/", views.contact_list, name="contact-list"),
    path("contacts/<uuid:contact_id>/", views.contact_detail, name="contact-detail"),

    # ── Auto Reply Rules ──────────────────────────────────────────────────────
    path("auto-replies/", views.auto_reply_rule_list, name="auto-reply-list"),
    path("auto-replies/test/", views.test_auto_reply, name="auto-reply-test"),
    path("auto-replies/<uuid:rule_id>/", views.auto_reply_rule_detail, name="auto-reply-detail"),
]