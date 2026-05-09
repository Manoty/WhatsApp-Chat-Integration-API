# 📡 WhatsApp Chat Integration API
## Complete System Summary — All 16 Phases

---

## 🏗 Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        EXTERNAL LAYER                               │
│   WhatsApp Users    Meta / Twilio API    External Systems           │
└──────────┬───────────────────┬──────────────────┬───────────────────┘
           │                   │                  │
           ▼                   ▼                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      DJANGO / DAPHNE (ASGI)                         │
│                                                                     │
│  HTTP Endpoints (DRF)          WebSocket Feeds (Channels)           │
│  /api/webhook/whatsapp/        ws://.../ws/business/<id>/           │
│  /api/messages/send/           ws://.../ws/agent/<id>/              │
│  /api/conversations/           ws://.../ws/conversation/<id>/       │
│  /api/analytics/               (real-time push via Redis)           │
│  /api/templates/                                                    │
│  /api/auto-replies/                                                 │
│  /api/keys/ /api/agents/                                            │
│  /api/language/ /api/webhooks/                                      │
└──────────┬──────────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        SERVICE LAYER                                │
│                                                                     │
│  WebhookService      MessageService      TemplateService            │
│  AutoReplyEngine     MediaService        LanguageDetector           │
│  AssignmentEngine    LabelService        AnalyticsService           │
│  WebhookDispatcher   EventBuilder        APIKeyService              │
└──────────┬──────────────────────────────────────────────────────────┘
           │
     ┌─────┴──────┐
     ▼            ▼
┌─────────┐  ┌──────────────────────────────────────────────────────┐
│ SQLite  │  │              REDIS (Docker)                          │
│   or    │  │                                                      │
│Postgres │  │  Celery Broker    Channel Layer    Cache             │
│         │  │  (task queues)    (WS pub/sub)     (analytics)      │
└─────────┘  └──────────────────────────────────────────────────────┘
                          │
                          ▼
             ┌────────────────────────────┐
             │     CELERY WORKERS         │
             │                            │
             │  Queue: messages           │
             │  • send_whatsapp_message   │
             │  • send_whatsapp_media     │
             │  • send_template           │
             │  • process_auto_reply      │
             │                            │
             │  Queue: callbacks          │
             │  • update_message_status   │
             │                            │
             │  Queue: webhooks           │
             │  • deliver_webhook         │
             │                            │
             │  Queue: scheduled          │
             │  • daily_stats             │
             │  • cleanup_task_results    │
             │  • cleanup_expired_keys    │
             │  • analytics_snapshot      │
             └────────────────────────────┘
```

---

## 📦 Data Models

```
BusinessAccount (Tenant)
│  id, name, phone_number_id, whatsapp_token, is_active
│
├── APIKey
│     id, name, prefix, key_hash, scope, status,
│     expiry_at, last_used_at, request_count, allowed_ips
│
├── WhatsAppContact
│     id, phone_number, display_name, is_opted_in, last_seen
│     └── Conversation
│           id, status, assigned_to, last_message_at
│           ├── Message
│           │     id, direction, message_type, body,
│           │     provider_message_id, status,
│           │     detected_language, language_confidence
│           │     └── MediaAttachment
│           │           id, category, media_url, mime_type,
│           │           file_name, file_size, caption
│           └── ConversationLabel → Label
│                 id, name, colour, description
│
├── AutoReplyRule
│     id, name, keyword, match_type, language,
│     reply_text, is_fallback, priority, trigger_count
│
├── MessageTemplate
│     id, name, template_name, category, language,
│     body, variable_count, status, send_count
│     └── TemplateSend
│           id, contact, variables, rendered_body,
│           status, provider_message_id, sent_at
│
├── WebhookEndpoint
│     id, name, url, secret, subscribed_events,
│     total_deliveries, failed_deliveries
│     └── WebhookDeliveryLog
│           id, event_type, payload, status,
│           http_status_code, attempt_number, duration_ms
│
├── Label
│     id, name, colour, description, is_active
│
└── Agent
      id, name, email, status, max_conversations,
      total_assigned, total_resolved, last_assigned_at
      └── AssignmentLog
            id, agent, assigned_by, assignment_type,
            unassigned_at, unassignment_reason
```

---

## 🗺 Complete API Reference (All 16 Phases)

### System
| Method | Endpoint | Auth | Purpose |
|--------|----------|------|---------|
| GET | `/api/health/` | None | Health check |
| GET | `/api/stats/` | Key | Entity counts |

### Webhooks In
| Method | Endpoint | Auth | Purpose |
|--------|----------|------|---------|
| GET/POST | `/api/webhook/whatsapp/` | None | Receive WhatsApp messages |
| POST | `/api/messages/status/` | None | Delivery status callback |

### Messaging
| Method | Endpoint | Auth | Purpose |
|--------|----------|------|---------|
| POST | `/api/messages/send/` | Key | Send text (sync) |
| POST | `/api/messages/send/async/` | Key | Send text (async) |
| POST | `/api/messages/send/media/` | Key | Send media (async) |
| GET | `/api/messages/<id>/media/` | Key | Get message media |
| GET | `/api/tasks/<task_id>/` | Key | Track task status |

### Conversations
| Method | Endpoint | Auth | Purpose |
|--------|----------|------|---------|
| GET | `/api/conversations/` | Key | List + filter |
| GET/PATCH | `/api/conversations/<id>/` | Key | Detail + update |
| GET | `/api/conversations/<id>/messages/` | Key | Full thread |
| GET | `/api/conversations/<id>/media/` | Key | Media gallery |
| GET/POST/PUT/DELETE | `/api/conversations/<id>/labels/` | Key | Manage labels |
| POST | `/api/conversations/<id>/assign/` | Key | Manual assign |
| POST | `/api/conversations/<id>/unassign/` | Key | Remove assignment |
| GET | `/api/conversations/<id>/assignments/` | Key | Audit trail |

### Contacts
| Method | Endpoint | Auth | Purpose |
|--------|----------|------|---------|
| GET | `/api/contacts/` | Key | List + search |
| GET | `/api/contacts/<id>/` | Key | Detail + history |

### Auto-Reply Rules
| Method | Endpoint | Auth | Purpose |
|--------|----------|------|---------|
| GET/POST | `/api/auto-replies/` | Key | List + create |
| POST | `/api/auto-replies/test/` | Key | Dry-run with language |
| GET/PUT/PATCH/DELETE | `/api/auto-replies/<id>/` | Key | Manage rule |

### Templates
| Method | Endpoint | Auth | Purpose |
|--------|----------|------|---------|
| GET/POST | `/api/templates/` | Key | List + create |
| POST | `/api/templates/preview/` | Key | Render without sending |
| POST | `/api/templates/send/` | Key | Send to one contact |
| POST | `/api/templates/send/bulk/` | Key | Send to 1,000 contacts |
| GET/PATCH/DELETE | `/api/templates/<id>/` | Key | Manage template |
| POST | `/api/templates/<id>/submit/` | Key | Submit for approval |
| GET | `/api/templates/<id>/history/` | Key | Send audit log |

### Webhooks Out
| Method | Endpoint | Auth | Purpose |
|--------|----------|------|---------|
| GET | `/api/webhooks/events/` | Key | List event types |
| GET/POST | `/api/webhooks/endpoints/` | Key | List + create |
| GET/PATCH/DELETE | `/api/webhooks/endpoints/<id>/` | Key | Manage endpoint |
| POST | `/api/webhooks/endpoints/<id>/test/` | Key | Send test ping |
| GET | `/api/webhooks/endpoints/<id>/logs/` | Key | Delivery logs |

### API Keys
| Method | Endpoint | Auth | Purpose |
|--------|----------|------|---------|
| GET/POST | `/api/keys/` | Key | List + create |
| GET | `/api/keys/verify/` | Key | Verify current key |
| GET/PATCH/DELETE | `/api/keys/<id>/` | Key | Manage key |
| POST | `/api/keys/<id>/revoke/` | Key | Instant revoke |
| POST | `/api/keys/<id>/rotate/` | Key | Rotate key |
| GET | `/api/keys/<id>/stats/` | Key | Usage stats |

### Labels
| Method | Endpoint | Auth | Purpose |
|--------|----------|------|---------|
| GET/POST | `/api/labels/` | Key | List + create |
| GET/PATCH/DELETE | `/api/labels/<id>/` | Key | Manage label |

### Agents
| Method | Endpoint | Auth | Purpose |
|--------|----------|------|---------|
| GET/POST | `/api/agents/` | Key | List + create |
| GET | `/api/agents/workload/` | Key | Team dashboard |
| GET/PATCH/DELETE | `/api/agents/<id>/` | Key | Manage agent |
| GET | `/api/agents/<id>/workload/` | Key | Agent workload |

### Analytics
| Method | Endpoint | Auth | Purpose |
|--------|----------|------|---------|
| GET | `/api/analytics/overview/` | Key | KPI snapshot |
| GET | `/api/analytics/messages/` | Key | Volume over time |
| GET | `/api/analytics/conversations/` | Key | Open/close rates |
| GET | `/api/analytics/agents/` | Key | Agent performance |
| GET | `/api/analytics/contacts/` | Key | Growth + opt-ins |
| GET | `/api/analytics/auto-replies/` | Key | Rule performance |
| GET | `/api/analytics/templates/` | Key | Delivery rates |
| GET | `/api/analytics/response-time/` | Key | Response distribution |
| GET | `/api/analytics/full/` | Key | All sections combined |

### WebSocket
| Protocol | URL | Purpose |
|----------|-----|---------|
| WS | `/ws/business/<id>/` | Full business event feed |
| WS | `/ws/agent/<id>/` | Agent-specific feed |
| WS | `/ws/conversation/<id>/` | Conversation thread feed |
| GET | `/api/ws/info/` | Connection info + event list |

### Language
| Method | Endpoint | Auth | Purpose |
|--------|----------|------|---------|
| POST | `/api/language/detect/` | Key | Detect language |
| GET | `/api/language/supported/` | Key | List languages |
| GET | `/api/language/breakdown/` | Key | Message language stats |
| GET | `/api/language/coverage/` | Key | Rule gap analysis |

---

## 🔐 Security Summary

| Layer | Implementation |
|-------|---------------|
| API Auth | DB-backed API keys (SHA-256 hashed, never stored plaintext) |
| Key Scopes | read / write / admin per key |
| Key Expiry | Optional expiry date, auto-expire via Celery |
| IP Allowlist | Per-key IP restriction |
| Key Rotation | Atomic swap — old revoked, new issued |
| Webhook Auth | HMAC-SHA1 (Twilio) / HMAC-SHA256 (Meta) |
| Webhooks Out | HMAC-SHA256 signed payloads |
| WS Auth | API key via query param on connect |
| Rate Limiting | 60/min webhooks, 60/min sends, 300/min general |
| CORS | Configurable allowed origins |
| HTTPS | Enforced in production via security headers |
| Request Tracing | `X-Request-ID` on every response |

---

## ⚙️ Celery Task Reference

| Task | Queue | Trigger | Purpose |
|------|-------|---------|---------|
| `whatsapp.send_message` | messages | API call | Async text send |
| `whatsapp.send_media` | messages | API call | Async media send |
| `whatsapp.send_template` | messages | API call | Async template send |
| `whatsapp.auto_reply` | messages | Webhook | Auto-reply processing |
| `whatsapp.update_status` | callbacks | Status webhook | Update message status |
| `whatsapp.deliver_webhook` | webhooks | Any event | Outbound webhook delivery |
| `whatsapp.daily_stats` | scheduled | Midnight UTC | Stats snapshot |
| `whatsapp.cleanup_task_results` | scheduled | 2am UTC | DB cleanup |
| `whatsapp.cleanup_expired_keys` | scheduled | Hourly | Key expiry |
| `whatsapp.analytics_snapshot` | scheduled | Hourly | Analytics pre-compute |

---

## 🌐 WebSocket Event Reference

### Server → Client
| Event | Trigger |
|-------|---------|
| `connection.established` | On connect |
| `message.received` | Inbound WhatsApp message |
| `message.sent` | Outbound message sent |
| `message.delivered` | Provider delivery confirmation |
| `message.read` | Contact read the message |
| `message.failed` | Send failure |
| `message.status_changed` | Any other status change |
| `conversation.opened` | New conversation created |
| `conversation.closed` | Conversation closed |
| `conversation.updated` | Status/assignee changed |
| `conversation.assigned` | Agent assigned |
| `agent.created` | New agent added |
| `agent.status_changed` | Agent online/away/offline |
| `contact.created` | New contact auto-created |
| `pong` | Response to ping |

### Client → Server
| Action | Purpose |
|--------|---------|
| `ping` | Heartbeat |
| `typing.start` | Show typing indicator |
| `typing.stop` | Hide typing indicator |

---

## 🤖 Auto-Reply Language Matching

```
Inbound message
      │
      ▼ detect language
      │
      ├─► Pass 1: language-specific keyword rule   → reply in contact's language
      ├─► Pass 2: language-neutral keyword rule    → reply in any language
      ├─► Pass 3: language-specific fallback       → fallback in contact's language
      ├─► Pass 4: language-neutral fallback        → generic fallback
      └─► Silence
```

Supported: English, Swahili, French, Arabic, Spanish,
           Portuguese, German, Chinese, Hindi

---

## 🚀 Production Deployment Checklist

### Environment
- [ ] `DEBUG=False`
- [ ] `SECRET_KEY` — 50+ random chars, never committed
- [ ] `ALLOWED_HOSTS` — your domain only
- [ ] `WHATSAPP_MOCK_MODE=False`
- [ ] `TWILIO_WEBHOOK_VALIDATE=True`
- [ ] `META_APP_SECRET` — set for signature verification
- [ ] `API_KEYS` — remove, use DB keys only
- [ ] `LOG_LEVEL=INFO`

### Database
- [ ] Switch SQLite → PostgreSQL
- [ ] Set `CONN_MAX_AGE=60`
- [ ] Run `python manage.py migrate`
- [ ] Create superuser

### Infrastructure
- [ ] Redis running (Docker or managed)
- [ ] 4+ Celery workers running
- [ ] Celery Beat running
- [ ] Daphne (ASGI) behind Nginx/Caddy
- [ ] SSL/TLS certificate (HTTPS required by WhatsApp)
- [ ] Webhook URL registered in Twilio/Meta console

### Security
- [ ] Create first DB API key via shell
- [ ] Delete legacy `API_KEYS` from `.env`
- [ ] Set `TWILIO_WEBHOOK_VALIDATE=True`
- [ ] Configure `CORS_ALLOWED_ORIGINS`
- [ ] Set `SECURE_SSL_REDIRECT=True`

### Monitoring
- [ ] Flower dashboard secured with password
- [ ] Log aggregation configured (Datadog/CloudWatch)
- [ ] Uptime monitor on `/api/health/`
- [ ] Alert on Celery worker failures
- [ ] Alert on webhook delivery failure rate

---

## 📁 Final File Structure

```
whatsapp_api/
├── manage.py
├── requirements.txt
├── .env
│
├── whatsapp_api/
│   ├── settings.py
│   ├── urls.py
│   ├── wsgi.py
│   ├── asgi.py
│   ├── celery.py
│   └── __init__.py
│
└── whatsapp_integration/
    ├── models.py           — 12 models
    ├── views.py            — 50+ views
    ├── serializers.py      — 20+ serializers
    ├── urls.py             — 50+ routes
    ├── admin.py            — all models registered
    ├── apps.py             — signals wired
    ├── signals.py          — WS push on save
    ├── tasks.py            — 10 Celery tasks
    ├── authentication.py   — DB API key auth
    ├── exceptions.py       — uniform error format
    ├── middleware.py       — request logging
    ├── security.py         — HMAC verification
    ├── throttles.py        — rate limit classes
    ├── logging_formatters.py — JSON logs
    │
    ├── services/
    │   ├── webhook_service.py
    │   ├── message_service.py
    │   ├── media_service.py
    │   ├── template_service.py
    │   ├── auto_reply_engine.py
    │   ├── language_detector.py
    │   ├── language_service.py
    │   ├── assignment_engine.py
    │   ├── label_service.py
    │   ├── analytics_service.py
    │   ├── webhook_dispatcher.py
    │   ├── event_builder.py
    │   ├── api_key_service.py
    │   └── whatsapp_client.py
    │
    ├── ws/
    │   ├── routing.py
    │   ├── consumers.py
    │   ├── middleware.py
    │   └── channel_utils.py
    │
    └── migrations/
        ├── 0001_initial.py
        ├── 0002_autoreplyrule.py
        ├── 0003_mediaattachment.py
        ├── 0004_messagetemplate_templatesend.py
        ├── 0005_webhookendpoint_webhookdeliverylog.py
        ├── 0006_apikey.py
        ├── 0007_label_agent_conversationlabel_assignmentlog.py
        └── 0008_autoreplyrule_language_message_detected_language.py
```

---

## 🗺 Suggested Next Steps

### Immediate production wins
1. **Nginx + Gunicorn/Daphne** — reverse proxy setup
2. **PostgreSQL** — switch from SQLite
3. **Managed Redis** — Upstash (free tier) or Redis Cloud
4. **Environment secrets** — move to AWS Secrets Manager or Vault

### Feature extensions
1. **WhatsApp Flows** — interactive forms inside WhatsApp
2. **Broadcast Lists** — send to saved contact groups
3. **Chatbot NLP** — replace keyword matching with intent detection
4. **S3 Media Storage** — download + re-host provider media
5. **Multi-agent Chat** — agents joining same conversation
6. **SLA Timers** — alert when conversations exceed response time target
7. **Contact Import** — bulk CSV upload of contacts
8. **Reporting PDFs** — export analytics as PDF reports

### Scale when you need it
1. **Horizontal Celery scaling** — add workers per queue
2. **Read replicas** — PostgreSQL replica for analytics queries
3. **Redis Cluster** — for high-volume WebSocket deployments
4. **API versioning** — `/api/v2/` when breaking changes needed
5. **GraphQL layer** — add Strawberry on top of existing services