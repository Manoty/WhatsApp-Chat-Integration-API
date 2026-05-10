# 📡 WhatsApp Chat Integration API

A **production-grade WhatsApp messaging backend** built with Django + Django REST Framework. Receive inbound messages via webhook, store full conversation history, send automated replies, manage agents, run analytics, and push real-time events to dashboards via WebSocket — all in one system.

---

## 📋 Table of Contents

- [Features](#-features)
- [Architecture](#-architecture)
- [Tech Stack](#-tech-stack)
- [Project Structure](#-project-structure)
- [Getting Started](#-getting-started)
- [Configuration](#-configuration)
- [API Reference](#-api-reference)
- [WebSocket Events](#-websocket-events)
- [Auto-Reply Rules Engine](#-auto-reply-rules-engine)
- [Multi-Language Support](#-multi-language-support)
- [Agent Assignment](#-agent-assignment)
- [Analytics](#-analytics)
- [Security](#-security)
- [Celery Tasks](#-celery-tasks)
- [Deployment](#-deployment)
- [Roadmap](#-roadmap)

---

## ✨ Features

| Feature | Description |
|---|---|
| 📥 **Webhook Receiver** | Accepts inbound WhatsApp messages from Twilio or Meta |
| 💬 **Conversation Management** | Auto-creates and tracks threads per contact |
| 📤 **Message Sending** | Send text, media, and approved templates |
| 🤖 **Auto-Reply Engine** | Keyword rules with exact, contains, regex matching |
| 🌍 **Multi-Language** | Language detection + language-scoped reply rules |
| 🏢 **Multi-Tenant** | Full business account isolation |
| 🔐 **API Key Auth** | DB-backed keys with scopes, expiry, IP allowlist, rotation |
| ✍️ **Signature Verification** | HMAC validation for Twilio and Meta webhooks |
| 🚦 **Rate Limiting** | Per-endpoint throttling |
| 📊 **Analytics Dashboard** | Message volume, response times, agent stats, contact growth |
| ⚡ **Real-Time WebSocket** | Live event push to dashboards via Django Channels |
| 🎯 **Agent Assignment** | Round-robin auto-assignment with capacity limits |
| 🏷️ **Labels** | Tag conversations for triage and filtering |
| 📨 **Webhooks Out** | Notify external systems on any event with HMAC signing |
| 🎨 **Templates** | Send approved WhatsApp templates with dynamic variables |
| 📎 **Media Messages** | Receive and send images, audio, video, documents |
| ⚙️ **Async Processing** | Celery + Redis for all sends, retries, and scheduling |
| 📋 **Structured Logging** | JSON logs compatible with Datadog, CloudWatch, Papertrail |

---

## 🏗 Architecture

```
WhatsApp User
      │
      ▼
POST /api/webhook/whatsapp/
      │
      ▼
┌─────────────────────────────────────────────────────┐
│                  SERVICE LAYER                      │
│                                                     │
│  WebhookService → stores message                    │
│       │                                             │
│       ├─► LanguageDetector (detects language)       │
│       ├─► AutoReplyEngine  (matches rules)          │
│       ├─► AssignmentEngine (round-robin agent)      │
│       ├─► WebhookDispatcher (notifies externals)    │
│       └─► Channel Layer (WebSocket push)            │
└───────────────────────┬─────────────────────────────┘
                        │
          ┌─────────────┼─────────────┐
          ▼             ▼             ▼
      PostgreSQL      Redis        Celery
      (data store)   (broker +    (async tasks
                      WS layer)    + scheduler)
```

### Data Model

```
BusinessAccount (Tenant)
│
├── APIKey               (scoped, expiring, rotatable)
├── AutoReplyRule        (keyword + language rules)
├── MessageTemplate      (approved templates + sends)
├── WebhookEndpoint      (outbound event subscribers)
├── Label                (coloured conversation tags)
└── Agent                (support agents + assignment logs)
    │
    └── WhatsAppContact
            │
            └── Conversation
                    │
                    ├── Message (inbound/outbound + language)
                    │       └── MediaAttachment
                    └── ConversationLabel
```

---

## ⚙️ Tech Stack

| Layer | Technology |
|---|---|
| Backend | Django 4.2 + Django REST Framework |
| Async/WS | Django Channels 4 + Daphne |
| Task Queue | Celery 5 + Redis |
| Database | SQLite (dev) / PostgreSQL (production) |
| WhatsApp | Twilio WhatsApp / Meta Business API |
| Language Detection | `langdetect` (offline, no API key needed) |
| Authentication | Custom DB-backed API Key (SHA-256 hashed) |
| Logging | Structured JSON via custom formatter |

---

## 📁 Project Structure

```
whatsapp_api/
├── manage.py
├── requirements.txt
├── .env
│
├── whatsapp_api/               # Django project config
│   ├── settings.py
│   ├── urls.py
│   ├── asgi.py                 # Channels ASGI entry point
│   └── celery.py               # Celery app
│
└── whatsapp_integration/       # Main application
    ├── models.py               # 12 models
    ├── views.py                # 50+ endpoint handlers
    ├── serializers.py          # 20+ serializers
    ├── urls.py                 # 50+ routes
    ├── admin.py                # All models registered
    ├── apps.py                 # Signal registration
    ├── signals.py              # WebSocket push on save
    ├── tasks.py                # 10 Celery tasks
    ├── authentication.py       # DB API key auth backend
    ├── exceptions.py           # Uniform error envelope
    ├── middleware.py           # Request logging + X-Request-ID
    ├── security.py             # HMAC webhook verification
    ├── throttles.py            # Custom rate limit classes
    ├── logging_formatters.py   # JSON log formatter
    │
    ├── services/
    │   ├── webhook_service.py      # Inbound message processing
    │   ├── message_service.py      # Outbound orchestration
    │   ├── media_service.py        # Media parsing + sending
    │   ├── template_service.py     # Template lifecycle
    │   ├── auto_reply_engine.py    # Multi-pass rule matcher
    │   ├── language_detector.py    # langdetect wrapper
    │   ├── language_service.py     # Language API layer
    │   ├── assignment_engine.py    # Round-robin assignment
    │   ├── label_service.py        # Label management
    │   ├── analytics_service.py    # All analytics queries
    │   ├── webhook_dispatcher.py   # Outbound event dispatch
    │   ├── event_builder.py        # Versioned event payloads
    │   ├── api_key_service.py      # Key lifecycle management
    │   └── whatsapp_client.py      # Twilio / Mock provider
    │
    ├── ws/                         # Django Channels
    │   ├── routing.py              # WebSocket URL patterns
    │   ├── consumers.py            # Business / Agent / Conv consumers
    │   ├── middleware.py           # WS API key authentication
    │   └── channel_utils.py        # Push helper functions
    │
    └── migrations/                 # 8 migration files
```

---

## 🚀 Getting Started

### Prerequisites

- Python 3.11+
- Docker Desktop (for Redis)
- pip

### 1. Clone

```bash
git clone https://github.com/your-username/whatsapp-chat-api.git
cd whatsapp-chat-api
```

### 2. Virtual Environment

```bash
python -m venv venv
source venv/bin/activate        # Mac/Linux
# venv\Scripts\activate         # Windows
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Start Redis

```bash
docker run -d \
  --name whatsapp-redis \
  -p 6379:6379 \
  --restart unless-stopped \
  redis:7-alpine
```

### 5. Configure Environment

```bash
cp .env.example .env
# Edit .env with your values
```

### 6. Run Migrations

```bash
python manage.py migrate
python manage.py createsuperuser
```

### 7. Seed a Business Account

```bash
python manage.py shell -c "
from whatsapp_integration.models import BusinessAccount
b = BusinessAccount.objects.create(
    name='My Business',
    phone_number_id='YOUR_WHATSAPP_NUMBER',
    whatsapp_token='mock-token',
)
print('Business ID:', b.id)
"
```

### 8. Create First API Key

```bash
python manage.py shell -c "
from whatsapp_integration.models import BusinessAccount
from whatsapp_integration.services.api_key_service import APIKeyService
biz = BusinessAccount.objects.first()
svc = APIKeyService()
api_key, raw_key = svc.create_key(str(biz.id), 'Primary Key', scope='admin')
print('RAW KEY (save now):', raw_key)
"
```

### 9. Start All Services

```bash
# Terminal 1 — ASGI server
daphne -b 0.0.0.0 -p 8000 whatsapp_api.asgi:application

# Terminal 2 — Celery worker
celery -A whatsapp_api worker --loglevel=info \
  --queues=messages,callbacks,scheduled,webhooks

# Terminal 3 — Celery beat (scheduler)
celery -A whatsapp_api beat --loglevel=info \
  --scheduler django_celery_beat.schedulers:DatabaseScheduler

# Terminal 4 — Flower (task monitor)
celery -A whatsapp_api flower --port=5555
```

- Flower dashboard → **http://localhost:5555**
- Django Admin → **http://localhost:8000/admin/**

---

## 🔧 Configuration

### `.env` Reference

```env
# Core
DEBUG=True
SECRET_KEY=your-very-long-random-secret-key
ALLOWED_HOSTS=localhost,127.0.0.1
CORS_ALLOWED_ORIGINS=http://localhost:3000

# API Authentication
API_KEYS=dev-key-12345              # Legacy fallback only — use DB keys in prod

# WhatsApp / Twilio
WHATSAPP_VERIFY_TOKEN=my_verify_token
WHATSAPP_MOCK_MODE=True             # False = real Twilio sends
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token
TWILIO_WEBHOOK_VALIDATE=False       # True in production

# Meta WhatsApp Business API
META_APP_SECRET=your_meta_app_secret

# Redis + Celery
CELERY_BROKER_URL=redis://localhost:6379/0

# Logging
LOG_LEVEL=DEBUG
```

### Mock vs Live Mode

| Setting | Behaviour |
|---|---|
| `WHATSAPP_MOCK_MODE=True` | Simulates sends — no real API calls, perfect for dev |
| `WHATSAPP_MOCK_MODE=False` | Uses real Twilio credentials |

---

## 📖 API Reference

All protected endpoints require:
```
X-API-Key: sk_live_your_key_here
```

### System

| Method | Endpoint | Auth | Purpose |
|---|---|---|---|
| GET | `/api/health/` | None | Health check |
| GET | `/api/stats/` | Key | Entity counts |

### Webhooks In

| Method | Endpoint | Auth | Purpose |
|---|---|---|---|
| GET/POST | `/api/webhook/whatsapp/` | None | Receive inbound messages |
| POST | `/api/messages/status/` | None | Delivery status callback |

**Receive a Twilio message:**
```bash
curl -X POST http://localhost:8000/api/webhook/whatsapp/ \
  -H "Content-Type: application/json" \
  -d '{
    "MessageSid": "SM123",
    "From": "whatsapp:+254712345678",
    "To": "whatsapp:+254700000000",
    "Body": "Hello, what are your prices?",
    "NumMedia": "0"
  }'
```

### Messaging

| Method | Endpoint | Auth | Purpose |
|---|---|---|---|
| POST | `/api/messages/send/` | Key | Send text (sync) |
| POST | `/api/messages/send/async/` | Key | Send text (async) |
| POST | `/api/messages/send/media/` | Key | Send media (async) |
| GET | `/api/messages/<id>/media/` | Key | Get media attachment |
| GET | `/api/tasks/<task_id>/` | Key | Track task status |

**Send a message:**
```bash
curl -X POST http://localhost:8000/api/messages/send/ \
  -H "Content-Type: application/json" \
  -H "X-API-Key: sk_live_xxx" \
  -d '{
    "business_id": "<uuid>",
    "to_number": "+254712345678",
    "body": "Hello from the API!"
  }'
```

### Conversations

| Method | Endpoint | Auth | Purpose |
|---|---|---|---|
| GET | `/api/conversations/` | Key | List + filter |
| GET/PATCH | `/api/conversations/<id>/` | Key | Detail + update |
| GET | `/api/conversations/<id>/messages/` | Key | Full thread |
| GET | `/api/conversations/<id>/media/` | Key | Media gallery |
| GET/POST/PUT/DELETE | `/api/conversations/<id>/labels/` | Key | Manage labels |
| POST | `/api/conversations/<id>/assign/` | Key | Manual assign |
| POST | `/api/conversations/<id>/unassign/` | Key | Remove assignment |
| GET | `/api/conversations/<id>/assignments/` | Key | Assignment audit trail |

**Filter conversations:**
```bash
curl "http://localhost:8000/api/conversations/?status=open&label=urgent" \
  -H "X-API-Key: sk_live_xxx"
```

### Contacts

| Method | Endpoint | Auth | Purpose |
|---|---|---|---|
| GET | `/api/contacts/` | Key | List + search |
| GET | `/api/contacts/<id>/` | Key | Detail + history |

### Auto-Reply Rules

| Method | Endpoint | Auth | Purpose |
|---|---|---|---|
| GET/POST | `/api/auto-replies/` | Key | List + create |
| POST | `/api/auto-replies/test/` | Key | Dry-run with language detection |
| GET/PUT/PATCH/DELETE | `/api/auto-replies/<id>/` | Key | Manage rule |

### Templates

| Method | Endpoint | Auth | Purpose |
|---|---|---|---|
| GET/POST | `/api/templates/` | Key | List + create |
| POST | `/api/templates/preview/` | Key | Render without sending |
| POST | `/api/templates/send/` | Key | Send to one contact |
| POST | `/api/templates/send/bulk/` | Key | Send to up to 1,000 contacts |
| GET/PATCH/DELETE | `/api/templates/<id>/` | Key | Manage template |
| POST | `/api/templates/<id>/submit/` | Key | Submit for approval |
| GET | `/api/templates/<id>/history/` | Key | Send audit log |

**Create and send a template:**
```bash
# 1. Create
curl -X POST http://localhost:8000/api/templates/ \
  -H "Content-Type: application/json" \
  -H "X-API-Key: sk_live_xxx" \
  -d '{
    "business": "<uuid>",
    "name": "Order Confirmation",
    "template_name": "order_confirmation",
    "category": "utility",
    "language": "en",
    "body": "Hello {{1}}! Your order {{2}} for {{3}} is confirmed 🎉"
  }'

# 2. Submit (auto-approves in mock mode)
curl -X POST http://localhost:8000/api/templates/<id>/submit/ \
  -H "X-API-Key: sk_live_xxx"

# 3. Send
curl -X POST http://localhost:8000/api/templates/send/ \
  -H "Content-Type: application/json" \
  -H "X-API-Key: sk_live_xxx" \
  -d '{
    "business_id": "<uuid>",
    "to_number": "+254712345678",
    "template_name": "order_confirmation",
    "language": "en",
    "variables": ["John", "ORD-001", "KES 2,500"]
  }'
```

### Webhooks Out

| Method | Endpoint | Auth | Purpose |
|---|---|---|---|
| GET | `/api/webhooks/events/` | Key | List event types |
| GET/POST | `/api/webhooks/endpoints/` | Key | List + create endpoints |
| GET/PATCH/DELETE | `/api/webhooks/endpoints/<id>/` | Key | Manage endpoint |
| POST | `/api/webhooks/endpoints/<id>/test/` | Key | Send test ping |
| GET | `/api/webhooks/endpoints/<id>/logs/` | Key | Delivery logs |

**Register an endpoint:**
```bash
curl -X POST http://localhost:8000/api/webhooks/endpoints/ \
  -H "Content-Type: application/json" \
  -H "X-API-Key: sk_live_xxx" \
  -d '{
    "business": "<uuid>",
    "name": "CRM Integration",
    "url": "https://mycrm.com/whatsapp/events",
    "secret": "my-signing-secret",
    "subscribed_events": ["message.received", "conversation.opened"]
  }'
```

Use `["*"]` in `subscribed_events` to receive all event types.

**Verify incoming signatures in your receiver:**
```python
import hmac, hashlib

def verify_webhook(payload_bytes: bytes, signature_header: str, secret: str) -> bool:
    if not signature_header.startswith("sha256="):
        return False
    received = signature_header[len("sha256="):]
    expected = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)
```

### API Keys

| Method | Endpoint | Auth | Purpose |
|---|---|---|---|
| GET/POST | `/api/keys/` | Key | List + create keys |
| GET | `/api/keys/verify/` | Key | Verify current key |
| GET/PATCH/DELETE | `/api/keys/<id>/` | Key | Manage key |
| POST | `/api/keys/<id>/revoke/` | Key | Instant revocation |
| POST | `/api/keys/<id>/rotate/` | Key | Rotate (old revoked, new issued) |
| GET | `/api/keys/<id>/stats/` | Key | Usage stats |

**Create a scoped key with expiry:**
```bash
curl -X POST http://localhost:8000/api/keys/ \
  -H "Content-Type: application/json" \
  -H "X-API-Key: sk_live_xxx" \
  -d '{
    "business_id": "<uuid>",
    "name": "Dashboard Read Key",
    "scope": "read",
    "expiry_at": "2027-12-31T23:59:59Z"
  }'
# ⚠️ Raw key is shown ONCE — save it immediately
```

### Labels & Agents

| Method | Endpoint | Auth | Purpose |
|---|---|---|---|
| GET/POST | `/api/labels/` | Key | List + create labels |
| GET/PATCH/DELETE | `/api/labels/<id>/` | Key | Manage label |
| GET/POST | `/api/agents/` | Key | List + create agents |
| GET | `/api/agents/workload/` | Key | Team workload dashboard |
| GET/PATCH/DELETE | `/api/agents/<id>/` | Key | Manage agent |
| GET | `/api/agents/<id>/workload/` | Key | Single agent workload |

### Analytics

| Method | Endpoint | Auth | Purpose |
|---|---|---|---|
| GET | `/api/analytics/overview/` | Key | KPI snapshot + period deltas |
| GET | `/api/analytics/messages/` | Key | Volume over time |
| GET | `/api/analytics/conversations/` | Key | Open/close rates |
| GET | `/api/analytics/agents/` | Key | Agent performance |
| GET | `/api/analytics/contacts/` | Key | Growth + opt-in rates |
| GET | `/api/analytics/auto-replies/` | Key | Rule trigger rates |
| GET | `/api/analytics/templates/` | Key | Delivery + read rates |
| GET | `/api/analytics/response-time/` | Key | Response distribution |
| GET | `/api/analytics/full/` | Key | All sections in one call |

**Query parameters (all analytics endpoints):**
```
?business_id=<uuid>
?date_from=2026-05-01T00:00:00Z
?date_to=2026-05-31T23:59:59Z
?granularity=hour|day|week|month    (messages endpoint only)
```

### Language

| Method | Endpoint | Auth | Purpose |
|---|---|---|---|
| POST | `/api/language/detect/` | Key | Detect language of any text |
| GET | `/api/language/supported/` | Key | List supported languages |
| GET | `/api/language/breakdown/` | Key | Message language distribution |
| GET | `/api/language/coverage/` | Key | Rule gap analysis + recommendations |

### WebSocket Info

| Method | Endpoint | Auth | Purpose |
|---|---|---|---|
| GET | `/api/ws/info/` | Key | Connection URLs + event list |

---

## 🔌 WebSocket Events

Connect with your API key as a query parameter:

```javascript
const ws = new WebSocket(
  "ws://localhost:8000/ws/business/<business_id>/?api_key=sk_live_xxx"
);
ws.onmessage = (e) => console.log(JSON.parse(e.data));
```

**Three feed types:**

| URL | Audience | Events |
|---|---|---|
| `/ws/business/<id>/` | Supervisors, dashboards | All business events |
| `/ws/agent/<id>/` | Individual agents | Their assigned conversations |
| `/ws/conversation/<id>/` | Chat UIs | One thread |

**Events pushed server → client:**

| Event | Trigger |
|---|---|
| `connection.established` | On connect |
| `message.received` | Inbound WhatsApp message |
| `message.sent` | Outbound message sent |
| `message.delivered` | Provider delivery confirmation |
| `message.read` | Contact read the message |
| `message.failed` | Send failure |
| `conversation.opened` | New conversation created |
| `conversation.closed` | Conversation closed |
| `conversation.assigned` | Agent assigned |
| `agent.status_changed` | Agent online/away/offline |
| `contact.created` | New contact auto-created |
| `pong` | Response to client ping |

**Client → server actions:**
```javascript
// Heartbeat
ws.send(JSON.stringify({ action: "ping", data: { ts: Date.now() } }));

// Typing indicator
ws.send(JSON.stringify({
  action: "typing.start",
  data: { conversation_id: "<id>", agent: "alice@company.com" }
}));
```

**Close codes:**

| Code | Reason |
|---|---|
| `4001` | Authentication failed |
| `4003` | Access denied to this resource |

**Minimal React hook:**
```javascript
function useWhatsAppSocket(businessId, apiKey, onEvent) {
  const ws = useRef(null);
  useEffect(() => {
    const connect = () => {
      ws.current = new WebSocket(
        `ws://localhost:8000/ws/business/${businessId}/?api_key=${apiKey}`
      );
      ws.current.onmessage = (e) => onEvent(JSON.parse(e.data));
      ws.current.onclose = (e) => {
        if (e.code !== 4001 && e.code !== 4003) {
          setTimeout(connect, 3000); // auto-reconnect
        }
      };
    };
    connect();
    return () => ws.current?.close();
  }, [businessId, apiKey]);
}
```

---

## 🤖 Auto-Reply Rules Engine

Rules are evaluated in priority order after every inbound message. First match wins.

### Match Types

| Type | Behaviour | Example Keyword |
|---|---|---|
| `exact` | Full message equals keyword | `"pricing"` |
| `contains` | Keyword appears anywhere | `"price"` |
| `startswith` | Message begins with keyword | `"help"` |
| `regex` | Full Python regex pattern | `"^(hi\|hello\|hey)$"` |

### Create a Rule

```bash
curl -X POST http://localhost:8000/api/auto-replies/ \
  -H "Content-Type: application/json" \
  -H "X-API-Key: sk_live_xxx" \
  -d '{
    "business": "<uuid>",
    "name": "Pricing Reply",
    "keyword": "pricing",
    "match_type": "contains",
    "language": "en",
    "reply_text": "Our plans start at KES 1,500/month. Reply DEMO for a free trial!",
    "priority": 1,
    "is_fallback": false,
    "is_active": true
  }'
```

### Fallback Rule

Set `is_fallback: true` to fire when no keyword rule matches. One fallback per language recommended.

### Dry Run (Test Without Sending)

```bash
curl -X POST http://localhost:8000/api/auto-replies/test/ \
  -H "Content-Type: application/json" \
  -H "X-API-Key: sk_live_xxx" \
  -d '{"business_id": "<uuid>", "message": "what are your prices?"}'
```

---

## 🌍 Multi-Language Support

Language is detected automatically on every inbound message using `langdetect` (offline, no API key needed).

**Supported languages:** English, Swahili, French, Arabic, Spanish, Portuguese, German, Chinese, Hindi

### Matching Passes

```
Inbound: "Habari, bei yako ni ngapi?"   → detected: sw (Swahili, 85.7%)
  │
  ├─► Pass 1: Swahili keyword rule     → sends Swahili reply ✅
  ├─► Pass 2: Language-neutral rule    (skipped — matched above)
  ├─► Pass 3: Swahili fallback         (skipped)
  └─► Pass 4: Neutral fallback         (skipped)
```

### Detect Language

```bash
curl -X POST http://localhost:8000/api/language/detect/ \
  -H "Content-Type: application/json" \
  -H "X-API-Key: sk_live_xxx" \
  -d '{"text": "Habari, bei yako ni ngapi?"}'
# → {"language": "sw", "language_name": "Swahili", "confidence": 0.857}
```

### Coverage Analysis

```bash
curl "http://localhost:8000/api/language/coverage/?business_id=<uuid>" \
  -H "X-API-Key: sk_live_xxx"
# Shows which languages have rules and which have gaps + recommendations
```

---

## 👥 Agent Assignment

Conversations are automatically assigned to agents on creation using **round-robin**.

**Algorithm:**
1. Filter agents: `ONLINE` + under `max_conversations` capacity
2. Order by `last_assigned_at` ASC (never-assigned goes first)
3. Assign first agent in list

### Create an Agent

```bash
curl -X POST http://localhost:8000/api/agents/ \
  -H "Content-Type: application/json" \
  -H "X-API-Key: sk_live_xxx" \
  -d '{
    "business": "<uuid>",
    "name": "Alice Wanjiru",
    "email": "alice@company.com",
    "max_conversations": 10
  }'
```

### Manual Assignment

```bash
curl -X POST http://localhost:8000/api/conversations/<id>/assign/ \
  -H "Content-Type: application/json" \
  -H "X-API-Key: sk_live_xxx" \
  -d '{"agent_id": "<uuid>", "assigned_by": "supervisor@company.com"}'
```

### Team Workload Dashboard

```bash
curl "http://localhost:8000/api/agents/workload/?business_id=<uuid>" \
  -H "X-API-Key: sk_live_xxx"
```

```json
{
    "agent_count": 3,
    "available": 2,
    "agents": [
        {"name": "Alice", "active_conversations": 3, "capacity_pct": 30, "is_available": true},
        {"name": "Bob",   "active_conversations": 8, "capacity_pct": 80, "is_available": true},
        {"name": "Carol", "active_conversations": 10,"capacity_pct": 100,"is_available": false}
    ]
}
```

---

## 📊 Analytics

All endpoints support custom date ranges and return chart-ready JSON.

```bash
curl "http://localhost:8000/api/analytics/overview/?business_id=<uuid>" \
  -H "X-API-Key: sk_live_xxx"
```

```json
{
    "period": {"from": "...", "to": "...", "days": 30},
    "messages": {"total": 1240, "inbound": 820, "outbound": 420, "delta_pct": 18.3},
    "conversations": {"total": 340, "open": 45, "delta_pct": 12.0},
    "response_time": {"avg_seconds": 47, "avg_human": "47s"},
    "auto_reply_rate": 68.4
}
```

**Message volume series (chart-ready):**
```bash
curl "http://localhost:8000/api/analytics/messages/?granularity=day&date_from=2026-05-01T00:00:00Z" \
  -H "X-API-Key: sk_live_xxx"
```

```json
{
    "volume_series": [
        {"period": "2026-05-01", "inbound": 42, "outbound": 31},
        {"period": "2026-05-02", "inbound": 38, "outbound": 29}
    ],
    "peak_hours": [
        {"hour": 9, "count": 87},
        {"hour": 14, "count": 73}
    ]
}
```

---

## 🔐 Security

### API Key Scopes

| Scope | Permissions |
|---|---|
| `read` | GET requests only |
| `write` | GET + POST + PUT + PATCH + DELETE |
| `admin` | Full access including key management |

### Keys Are Never Stored in Plaintext

Keys are stored as SHA-256 hashes. The raw key (`sk_live_...`) is shown **once** on creation and can never be retrieved again. Rotate if lost.

### Webhook Signature Verification

| Provider | Algorithm | Header |
|---|---|---|
| Twilio | HMAC-SHA1 | `X-Twilio-Signature` |
| Meta | HMAC-SHA256 | `X-Hub-Signature-256` |
| Webhooks Out | HMAC-SHA256 | `X-Webhook-Signature` |

Enable in production:
```env
TWILIO_WEBHOOK_VALIDATE=True
META_APP_SECRET=your_app_secret
```

### Rate Limits

| Endpoint | Limit |
|---|---|
| Webhook inbound | 120 req/min |
| Send message | 60 req/min |
| Authenticated APIs | 300 req/min |
| Anonymous | 60 req/min |

### Request Tracing

Every response includes `X-Request-ID` for log correlation.

---

## ⚙️ Celery Tasks

| Task | Queue | Trigger | Retries |
|---|---|---|---|
| `whatsapp.send_message` | messages | API call | 3x exp backoff |
| `whatsapp.send_media` | messages | API call | 3x exp backoff |
| `whatsapp.send_template` | messages | API call | 3x exp backoff |
| `whatsapp.auto_reply` | messages | Inbound webhook | 2x |
| `whatsapp.update_status` | callbacks | Status callback | 2x |
| `whatsapp.deliver_webhook` | webhooks | Any event | 3x (60s→5m→15m) |
| `whatsapp.daily_stats` | scheduled | Midnight UTC | — |
| `whatsapp.cleanup_task_results` | scheduled | 2am UTC | — |
| `whatsapp.cleanup_expired_keys` | scheduled | Hourly | — |
| `whatsapp.analytics_snapshot` | scheduled | Hourly | — |

---

## 🚢 Deployment

### Production `.env`

```env
DEBUG=False
SECRET_KEY=<50+ random chars — never commit>
ALLOWED_HOSTS=yourdomain.com
WHATSAPP_MOCK_MODE=False
TWILIO_WEBHOOK_VALIDATE=True
META_APP_SECRET=<your_secret>
API_KEYS=                       # Leave empty — use DB keys only
LOG_LEVEL=INFO
```

### Production Checklist

**Environment**
- [ ] `DEBUG=False` + strong `SECRET_KEY`
- [ ] `WHATSAPP_MOCK_MODE=False`
- [ ] `TWILIO_WEBHOOK_VALIDATE=True` + `META_APP_SECRET` set
- [ ] `API_KEYS` empty — use DB-backed keys only

**Database**
- [ ] Switch SQLite → PostgreSQL
- [ ] Run `python manage.py migrate`

**Infrastructure**
- [ ] Redis running (Docker or managed e.g. Upstash)
- [ ] Daphne behind Nginx/Caddy with SSL
- [ ] Celery workers + Beat running as services
- [ ] Flower dashboard password-protected

**WhatsApp**
- [ ] Webhook URL registered in Twilio/Meta console
- [ ] Status callback URL registered
- [ ] SSL certificate active (HTTPS required by WhatsApp)

**Monitoring**
- [ ] Uptime monitor on `/api/health/`
- [ ] Log aggregation (Datadog / CloudWatch / Papertrail)
- [ ] Alert on Celery worker failures
- [ ] Alert on webhook delivery failure rate

### With Docker

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN python manage.py collectstatic --noinput
EXPOSE 8000
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "whatsapp_api.asgi:application"]
```

### PostgreSQL

```python
# settings.py
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME"),
        "USER": os.getenv("DB_USER"),
        "PASSWORD": os.getenv("DB_PASSWORD"),
        "HOST": os.getenv("DB_HOST", "localhost"),
        "PORT": os.getenv("DB_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }
}
```

### Webhook Setup

**Twilio:**
1. Console → Messaging → WhatsApp Senders
2. Webhook URL: `https://yourdomain.com/api/webhook/whatsapp/`
3. Status Callback: `https://yourdomain.com/api/messages/status/`

**Meta:**
1. Developer Console → App → WhatsApp → Configuration
2. Callback URL: `https://yourdomain.com/api/webhook/whatsapp/`
3. Verify Token: matches `WHATSAPP_VERIFY_TOKEN` in `.env`
4. Subscribe to `messages` field

**Local testing with ngrok:**
```bash
ngrok http 8000
# Use generated HTTPS URL as your webhook
```

---

## 🗺 Roadmap

- [ ] **WhatsApp Flows** — interactive forms inside WhatsApp
- [ ] **Broadcast Lists** — send to saved contact groups
- [ ] **NLP Intent Detection** — ML-based intent matching
- [ ] **S3 Media Storage** — download + re-host provider media
- [ ] **Multi-agent Chat** — multiple agents per conversation
- [ ] **SLA Timers** — alert on response time breaches
- [ ] **Contact Import** — bulk CSV upload
- [ ] **PDF Reports** — export analytics as downloadable PDF
- [ ] **GraphQL Layer** — add Strawberry on top of existing services
- [ ] **Horizontal Celery Scaling** — dedicated workers per queue

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

## 🤝 Contributing

1. Fork the repository
2. Create your feature branch: `git checkout -b feature/amazing-feature`
3. Commit: `git commit -m 'Add amazing feature'`
4. Push: `git push origin feature/amazing-feature`
5. Open a Pull Request

---

<p align="center">
  Built across 16 phases · Django + Channels · Celery + Redis · Twilio / Meta WhatsApp Business API
</p>