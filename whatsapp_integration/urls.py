from django.urls import path
from . import views

app_name = "whatsapp_integration"

urlpatterns = [
    # ── System ────────────────────────────────────────────────────────────────
    path("health/",  views.health_check,  name="health-check"),
    path("stats/",   views.system_stats,  name="system-stats"),

    # ── Webhook Inbound ───────────────────────────────────────────────────────
    path("webhook/whatsapp/", views.webhook_receiver, name="webhook-receiver"),

    # ── Messaging ─────────────────────────────────────────────────────────────
    path("messages/send/",                    views.send_message,            name="send-message"),
    path("messages/send/async/",              views.send_message_async,      name="send-message-async"),
    path("messages/send/media/",              views.send_media_message,      name="send-media-message"),
    path("messages/status/",                  views.message_status_callback, name="message-status-callback"),
    path("messages/<uuid:message_id>/media/", views.message_media,           name="message-media"),

    # ── Task Tracking ─────────────────────────────────────────────────────────
    path("tasks/<str:task_id>/", views.task_status, name="task-status"),

    # ── Conversations ─────────────────────────────────────────────────────────
    path("conversations/",                                      views.conversation_list,             name="conversation-list"),
    path("conversations/<uuid:conversation_id>/",               views.conversation_detail,           name="conversation-detail"),
    path("conversations/<uuid:conversation_id>/messages/",      views.conversation_messages,         name="conversation-messages"),
    path("conversations/<uuid:conversation_id>/media/",         views.conversation_media,            name="conversation-media"),
    path("conversations/<uuid:conversation_id>/labels/",        views.conversation_label_manage,     name="conversation-labels"),
    path("conversations/<uuid:conversation_id>/assign/",        views.conversation_assign,           name="conversation-assign"),
    path("conversations/<uuid:conversation_id>/unassign/",      views.conversation_unassign,         name="conversation-unassign"),
    path("conversations/<uuid:conversation_id>/assignments/",   views.conversation_assignment_history, name="conversation-assignments"),

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

    # ── Webhooks Out ──────────────────────────────────────────────────────────
    path("webhooks/events/",                               views.webhook_event_types,    name="webhook-event-types"),
    path("webhooks/endpoints/",                            views.webhook_endpoint_list,  name="webhook-endpoint-list"),
    path("webhooks/endpoints/<uuid:endpoint_id>/",         views.webhook_endpoint_detail,name="webhook-endpoint-detail"),
    path("webhooks/endpoints/<uuid:endpoint_id>/test/",    views.webhook_endpoint_test,  name="webhook-endpoint-test"),
    path("webhooks/endpoints/<uuid:endpoint_id>/logs/",    views.webhook_delivery_logs,  name="webhook-delivery-logs"),

    # ── API Key Management ────────────────────────────────────────────────────
    path("keys/",                      views.api_key_list,   name="api-key-list"),
    path("keys/verify/",               views.api_key_verify, name="api-key-verify"),
    path("keys/<uuid:key_id>/",        views.api_key_detail, name="api-key-detail"),
    path("keys/<uuid:key_id>/revoke/", views.api_key_revoke, name="api-key-revoke"),
    path("keys/<uuid:key_id>/rotate/", views.api_key_rotate, name="api-key-rotate"),
    path("keys/<uuid:key_id>/stats/",  views.api_key_stats,  name="api-key-stats"),

    # ── Labels ────────────────────────────────────────────────────────────────
    path("labels/",                views.label_list,   name="label-list"),
    path("labels/<uuid:label_id>/", views.label_detail, name="label-detail"),

    # ── Agents ────────────────────────────────────────────────────────────────
    path("agents/",                              views.agent_list,     name="agent-list"),
    path("agents/workload/",                     views.team_workload,  name="team-workload"),
    path("agents/<uuid:agent_id>/",              views.agent_detail,   name="agent-detail"),
    path("agents/<uuid:agent_id>/workload/",     views.agent_workload, name="agent-workload"),
    
    # ── Analytics ─────────────────────────────────────────────────────────────
    path("analytics/overview/",       views.analytics_overview,      name="analytics-overview"),
    path("analytics/messages/",       views.analytics_messages,      name="analytics-messages"),
    path("analytics/conversations/",  views.analytics_conversations, name="analytics-conversations"),
    path("analytics/agents/",         views.analytics_agents,        name="analytics-agents"),
    path("analytics/contacts/",       views.analytics_contacts,      name="analytics-contacts"),
    path("analytics/auto-replies/",   views.analytics_auto_replies,  name="analytics-auto-replies"),
    path("analytics/templates/",      views.analytics_templates,     name="analytics-templates"),
    path("analytics/response-time/",  views.analytics_response_time, name="analytics-response-time"),
    path("analytics/full/",           views.analytics_full,          name="analytics-full"),
]