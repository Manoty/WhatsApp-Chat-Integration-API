import uuid
from django.db import models
from django.utils import timezone


# ─── Helper ───────────────────────────────────────────────────────────────────

class TimeStampedModel(models.Model):
    """
    Abstract base model that gives every model
    created_at and updated_at for free.
    """
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# ─── Tenant Layer ─────────────────────────────────────────────────────────────

class BusinessAccount(TimeStampedModel):
    """
    Represents one business/tenant using this platform.
    In a SaaS context each customer gets one BusinessAccount.
    Isolation: contacts and conversations are scoped to this account.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    phone_number_id = models.CharField(
        max_length=64,
        unique=True,
        help_text="WhatsApp Business phone number ID from Meta or Twilio sender ID",
    )
    whatsapp_token = models.TextField(
        blank=True,
        help_text="API token for sending messages on behalf of this account",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "business_accounts"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.phone_number_id})"


# ─── Contact Layer ────────────────────────────────────────────────────────────

class WhatsAppContact(TimeStampedModel):
    """
    A real person who has messaged a BusinessAccount via WhatsApp.
    Scoped to one BusinessAccount — same phone number across
    two businesses = two separate contact records (correct SaaS behavior).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        BusinessAccount,
        on_delete=models.CASCADE,
        related_name="contacts",
    )
    phone_number = models.CharField(
        max_length=20,
        help_text="E.164 format e.g. +254712345678",
    )
    display_name = models.CharField(max_length=255, blank=True, default="")
    is_opted_in = models.BooleanField(
        default=True,
        help_text="Whether the contact has opted in to receive messages",
    )
    last_seen = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "whatsapp_contacts"
        # One phone number per business — no duplicates
        unique_together = ("business", "phone_number")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.display_name or self.phone_number} @ {self.business.name}"


# ─── Conversation Layer ───────────────────────────────────────────────────────

class Conversation(TimeStampedModel):
    """
    A thread between one WhatsAppContact and one BusinessAccount.
    There is exactly ONE active conversation per contact per business
    at any time (enforced by unique_together + status logic).
    """

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"
        PENDING = "pending", "Pending"   # waiting for human agent

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        BusinessAccount,
        on_delete=models.CASCADE,
        related_name="conversations",
    )
    contact = models.ForeignKey(
        WhatsAppContact,
        on_delete=models.CASCADE,
        related_name="conversations",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
    )
    last_message_at = models.DateTimeField(null=True, blank=True)
    # Optional: human agent assigned to this conversation
    assigned_to = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "conversations"
        ordering = ["-last_message_at"]

    def __str__(self):
        return f"Conv [{self.status}] | {self.contact} | {self.business.name}"

    def update_last_message_time(self):
        """Call this every time a new message is added."""
        self.last_message_at = timezone.now()
        self.save(update_fields=["last_message_at", "updated_at"])


# ─── Message Layer ────────────────────────────────────────────────────────────

class Message(TimeStampedModel):
    """
    A single WhatsApp message inside a Conversation.
    Tracks direction (inbound vs outbound), delivery status,
    and the raw provider payload for debugging.
    """

    class Direction(models.TextChoices):
        INBOUND = "inbound", "Inbound"    # contact → business
        OUTBOUND = "outbound", "Outbound" # business → contact

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"       # queued, not sent yet
        SENT = "sent", "Sent"                # accepted by provider
        DELIVERED = "delivered", "Delivered" # delivered to device
        READ = "read", "Read"                # contact opened it
        FAILED = "failed", "Failed"          # provider rejected

    class MessageType(models.TextChoices):
        TEXT = "text", "Text"
        IMAGE = "image", "Image"
        AUDIO = "audio", "Audio"
        VIDEO = "video", "Video"
        DOCUMENT = "document", "Document"
        LOCATION = "location", "Location"
        TEMPLATE = "template", "Template"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    direction = models.CharField(
        max_length=10,
        choices=Direction.choices,
    )
    message_type = models.CharField(
        max_length=20,
        choices=MessageType.choices,
        default=MessageType.TEXT,
    )
    # The actual message content
    body = models.TextField(blank=True, default="")
    # WhatsApp's own message ID (for deduplication + status callbacks)
    provider_message_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
        help_text="Message ID returned by WhatsApp/Twilio provider",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    # Store raw provider webhook payload — invaluable for debugging
    raw_payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="Raw JSON payload from provider webhook",
    )
    # When the provider says the message was sent/delivered/read
    status_updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "messages"
        ordering = ["created_at"]  # oldest first inside a conversation

    def __str__(self):
        return (
            f"[{self.direction.upper()}] {self.message_type} | "
            f"{self.status} | {self.created_at:%Y-%m-%d %H:%M}"
        )
        
        
# ─── Automation Layer ─────────────────────────────────────────────────────────

class AutoReplyRule(TimeStampedModel):
    """
    A single automation rule scoped to one BusinessAccount.

    Rules are evaluated in priority order (lowest number = checked first).
    The first matching rule wins — no multi-rule chaining.

    Match types:
      exact    — message body must equal keyword exactly (case-insensitive)
      contains — message body must contain the keyword (case-insensitive)
      startswith — message body must start with keyword (case-insensitive)
      regex    — full Python regex match against message body

    Special rule:
      is_fallback=True → fires when NO other rule matches.
      Only one fallback per business is meaningful.
    """

    class MatchType(models.TextChoices):
        EXACT = "exact", "Exact Match"
        CONTAINS = "contains", "Contains Keyword"
        STARTSWITH = "startswith", "Starts With"
        REGEX = "regex", "Regular Expression"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        BusinessAccount,
        on_delete=models.CASCADE,
        related_name="auto_reply_rules",
    )
    name = models.CharField(
        max_length=255,
        help_text="Human-readable name e.g. 'Pricing Enquiry Reply'",
    )
    keyword = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="The trigger keyword or pattern. Leave blank for fallback rules.",
    )
    match_type = models.CharField(
        max_length=20,
        choices=MatchType.choices,
        default=MatchType.CONTAINS,
    )
    reply_text = models.TextField(
        help_text="The message text to send when this rule matches.",
    )
    is_active = models.BooleanField(default=True)
    is_fallback = models.BooleanField(
        default=False,
        help_text="If True, this rule fires when no other rule matches.",
    )
    priority = models.PositiveIntegerField(
        default=10,
        help_text="Lower number = evaluated first. Range: 1 (highest) to 100 (lowest).",
    )
    # Track how many times this rule has fired (analytics)
    trigger_count = models.PositiveIntegerField(default=0, editable=False)

    class Meta:
        db_table = "auto_reply_rules"
        ordering = ["priority", "created_at"]

    def __str__(self):
        if self.is_fallback:
            return f"[FALLBACK] {self.name} @ {self.business.name}"
        return f"[{self.match_type}] '{self.keyword}' → {self.name}"

    def increment_trigger_count(self):
        """Thread-safe counter increment."""
        AutoReplyRule.objects.filter(id=self.id).update(
            trigger_count=models.F("trigger_count") + 1
        )        
        
# ─── Media Layer ──────────────────────────────────────────────────────────────

class MediaAttachment(TimeStampedModel):
    """
    Stores metadata about a media file attached to a Message.
    We store the provider's URL and metadata — we do NOT
    download/store the raw file (use S3 for that in production).

    One Message can have one MediaAttachment.
    """

    class MediaCategory(models.TextChoices):
        IMAGE    = "image",    "Image"
        AUDIO    = "audio",    "Audio"
        VIDEO    = "video",    "Video"
        DOCUMENT = "document", "Document"
        STICKER  = "sticker",  "Sticker"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    message = models.OneToOneField(
        Message,
        on_delete=models.CASCADE,
        related_name="media_attachment",
    )
    category = models.CharField(
        max_length=20,
        choices=MediaCategory.choices,
    )
    # URL where the file can be fetched from the provider
    media_url = models.URLField(
        max_length=2048,
        blank=True,
        default="",
        help_text="Provider-hosted URL for the media file",
    )
    # Provider's own media ID (used to fetch the URL for Meta API)
    provider_media_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
    )
    mime_type = models.CharField(
        max_length=127,
        blank=True,
        default="",
        help_text="e.g. image/jpeg, audio/ogg, application/pdf",
    )
    file_name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Original filename for documents",
    )
    file_size = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="File size in bytes",
    )
    caption = models.TextField(
        blank=True,
        default="",
        help_text="Optional caption sent with the media",
    )
    # If we download and re-host the file (e.g. on S3) store URL here
    stored_url = models.URLField(
        max_length=2048,
        blank=True,
        default="",
        help_text="Our own stored copy URL (e.g. S3). Empty if not downloaded.",
    )
    is_downloaded = models.BooleanField(default=False)

    class Meta:
        db_table = "media_attachments"
        ordering = ["-created_at"]

    def __str__(self):
        return (
            f"[{self.category}] {self.file_name or self.mime_type} "
            f"| msg={self.message_id}"
        )  
        
      
# ─── Template Layer ───────────────────────────────────────────────────────────

class MessageTemplate(TimeStampedModel):
    """
    A WhatsApp message template scoped to a BusinessAccount.

    Templates must be approved by Meta before sending.
    In Twilio, pre-approved templates are identified by name.

    Variable placeholders use {{1}}, {{2}}, {{3}} notation.
    Example body: "Hello {{1}}, your order {{2}} is ready!"
    """

    class Status(models.TextChoices):
        DRAFT    = "draft",    "Draft"       # not yet submitted
        PENDING  = "pending",  "Pending"     # submitted, awaiting Meta review
        APPROVED = "approved", "Approved"    # ready to send
        REJECTED = "rejected", "Rejected"    # Meta rejected it
        PAUSED   = "paused",   "Paused"      # approved but paused by Meta
        DISABLED = "disabled", "Disabled"    # disabled by Meta

    class Category(models.TextChoices):
        MARKETING     = "marketing",     "Marketing"
        UTILITY       = "utility",       "Utility"
        AUTHENTICATION = "authentication", "Authentication"

    class Language(models.TextChoices):
        ENGLISH    = "en",    "English"
        ENGLISH_US = "en_US", "English (US)"
        SWAHILI    = "sw",    "Swahili"
        FRENCH     = "fr",    "French"
        ARABIC     = "ar",    "Arabic"
        SPANISH    = "es",    "Spanish"
        PORTUGUESE = "pt_BR", "Portuguese (Brazil)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        BusinessAccount,
        on_delete=models.CASCADE,
        related_name="templates",
    )
    # Human-readable name for our system
    name = models.CharField(
        max_length=255,
        help_text="Internal name e.g. 'Order Confirmation'",
    )
    # Provider-side template name (snake_case, Meta requirement)
    template_name = models.CharField(
        max_length=512,
        help_text="Provider template name e.g. 'order_confirmation' (snake_case)",
    )
    category = models.CharField(
        max_length=30,
        choices=Category.choices,
        default=Category.UTILITY,
    )
    language = models.CharField(
        max_length=10,
        choices=Language.choices,
        default=Language.ENGLISH,
    )
    # The template body with {{1}} {{2}} variable placeholders
    body = models.TextField(
        help_text="Template body with {{1}}, {{2}} placeholders",
    )
    # Header (optional — text, image, video, document)
    header_text = models.CharField(max_length=60, blank=True, default="")
    header_media_url = models.URLField(max_length=2048, blank=True, default="")
    # Footer (optional)
    footer_text = models.CharField(max_length=60, blank=True, default="")
    # Number of variables in this template (auto-calculated on save)
    variable_count = models.PositiveIntegerField(default=0, editable=False)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    # Meta's own template ID (returned after submission)
    provider_template_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )
    rejection_reason = models.TextField(blank=True, default="")
    # Analytics
    send_count    = models.PositiveIntegerField(default=0, editable=False)
    success_count = models.PositiveIntegerField(default=0, editable=False)

    class Meta:
        db_table = "message_templates"
        ordering = ["-created_at"]
        unique_together = ("business", "template_name", "language")

    def __str__(self):
        return f"[{self.status.upper()}] {self.name} ({self.language})"

    def save(self, *args, **kwargs):
        # Auto-count variables on every save
        import re
        self.variable_count = len(re.findall(r"\{\{\d+\}\}", self.body))
        super().save(*args, **kwargs)

    def render(self, variables: list[str]) -> str:
        """
        Replace {{1}}, {{2}}, ... with provided variable values.
        Returns the rendered message body.
        """
        import re
        rendered = self.body
        for i, value in enumerate(variables, start=1):
            rendered = rendered.replace(f"{{{{{i}}}}}", str(value))
        # Warn if any placeholders remain unfilled
        remaining = re.findall(r"\{\{\d+\}\}", rendered)
        if remaining:
            import logging
            logging.getLogger(__name__).warning(
                "Template '%s' has unfilled variables: %s",
                self.template_name, remaining,
            )
        return rendered

    def increment_send_count(self, success: bool = True):
        """Thread-safe counters."""
        MessageTemplate.objects.filter(id=self.id).update(
            send_count=models.F("send_count") + 1
        )
        if success:
            MessageTemplate.objects.filter(id=self.id).update(
                success_count=models.F("success_count") + 1
            )


class TemplateSend(TimeStampedModel):
    """
    Tracks every individual template send attempt.
    One record per send — lets us audit who got what template and when.
    """

    class Status(models.TextChoices):
        QUEUED    = "queued",    "Queued"
        SENT      = "sent",      "Sent"
        DELIVERED = "delivered", "Delivered"
        READ      = "read",      "Read"
        FAILED    = "failed",    "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template = models.ForeignKey(
        MessageTemplate,
        on_delete=models.CASCADE,
        related_name="sends",
    )
    message = models.OneToOneField(
        Message,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="template_send",
        help_text="The Message record created when this template was sent",
    )
    contact = models.ForeignKey(
        WhatsAppContact,
        on_delete=models.CASCADE,
        related_name="template_sends",
    )
    # The actual variables used in this send
    variables      = models.JSONField(default=list)
    rendered_body  = models.TextField(blank=True, default="")
    status         = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.QUEUED,
    )
    provider_message_id = models.CharField(
        max_length=255, blank=True, default="", db_index=True
    )
    error_message  = models.TextField(blank=True, default="")
    sent_at        = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "template_sends"
        ordering = ["-created_at"]

    def __str__(self):
        return (
            f"{self.template.name} → {self.contact.phone_number} "
            f"[{self.status}]"
        )        
        
        
# ─── Webhooks Out Layer ───────────────────────────────────────────────────────

class WebhookEndpoint(TimeStampedModel):
    """
    An external URL registered by a BusinessAccount to receive event
    notifications. One business can have multiple endpoints
    (e.g. CRM + analytics + custom app).

    Events are POSTed to the URL as JSON with an HMAC-SHA256 signature
    in the X-Webhook-Signature header for verification.
    """

    class EventType(models.TextChoices):
        # Message events
        MESSAGE_RECEIVED  = "message.received",  "Message Received"
        MESSAGE_SENT      = "message.sent",      "Message Sent"
        MESSAGE_DELIVERED = "message.delivered", "Message Delivered"
        MESSAGE_READ      = "message.read",      "Message Read"
        MESSAGE_FAILED    = "message.failed",    "Message Failed"
        # Conversation events
        CONVERSATION_OPENED = "conversation.opened", "Conversation Opened"
        CONVERSATION_CLOSED = "conversation.closed", "Conversation Closed"
        # Contact events
        CONTACT_CREATED = "contact.created", "Contact Created"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        BusinessAccount,
        on_delete=models.CASCADE,
        related_name="webhook_endpoints",
    )
    name = models.CharField(
        max_length=255,
        help_text="Human label e.g. 'CRM Integration' or 'Analytics Pipeline'",
    )
    url = models.URLField(
        max_length=2048,
        help_text="HTTPS endpoint that will receive event POST requests",
    )
    # Secret used to sign outbound payloads (HMAC-SHA256)
    secret = models.CharField(
        max_length=255,
        help_text="Secret key used to sign webhook payloads",
    )
    # Which events this endpoint subscribes to
    # Stored as JSON list e.g. ["message.received", "conversation.opened"]
    subscribed_events = models.JSONField(
        default=list,
        help_text="List of event types this endpoint will receive",
    )
    is_active = models.BooleanField(default=True)
    # Stats
    total_deliveries  = models.PositiveIntegerField(default=0, editable=False)
    failed_deliveries = models.PositiveIntegerField(default=0, editable=False)
    last_triggered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "webhook_endpoints"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} → {self.url[:60]} [{self.business.name}]"

    def subscribes_to(self, event_type: str) -> bool:
        """Check if this endpoint wants this event."""
        return (
            event_type in self.subscribed_events
            or "*" in self.subscribed_events   # wildcard — all events
        )

    def increment_delivery(self, success: bool):
        WebhookEndpoint.objects.filter(id=self.id).update(
            total_deliveries=models.F("total_deliveries") + 1,
            last_triggered_at=timezone.now(),
        )
        if not success:
            WebhookEndpoint.objects.filter(id=self.id).update(
                failed_deliveries=models.F("failed_deliveries") + 1,
            )


class WebhookDeliveryLog(TimeStampedModel):
    """
    Immutable log of every webhook delivery attempt.
    One record per attempt — retries create new records.
    Invaluable for debugging and auditing.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILED  = "failed",  "Failed"
        RETRYING = "retrying", "Retrying"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    endpoint = models.ForeignKey(
        WebhookEndpoint,
        on_delete=models.CASCADE,
        related_name="delivery_logs",
    )
    event_type   = models.CharField(max_length=50)
    payload      = models.JSONField(help_text="Full event payload sent")
    status       = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    http_status_code = models.PositiveIntegerField(null=True, blank=True)
    response_body    = models.TextField(blank=True, default="")
    error_message    = models.TextField(blank=True, default="")
    attempt_number   = models.PositiveIntegerField(default=1)
    duration_ms      = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Response time in milliseconds",
    )
    delivered_at     = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "webhook_delivery_logs"
        ordering = ["-created_at"]

    def __str__(self):
        return (
            f"{self.event_type} → {self.endpoint.url[:40]} "
            f"[{self.status}] attempt={self.attempt_number}"
        )        
      
      
# ─── API Key Management ───────────────────────────────────────────────────────

class APIKey(TimeStampedModel):
    """
    A database-backed API key for authenticating requests.

    Keys are stored as SHA-256 hashes — the raw key is shown
    ONCE on creation and never stored in plaintext.

    Scopes:
      read  — GET requests only
      write — GET + POST + PUT + PATCH
      admin — full access including key management

    Status:
      active  — valid and usable
      revoked — manually invalidated
      expired — past expiry_at date
    """

    class Status(models.TextChoices):
        ACTIVE  = "active",  "Active"
        REVOKED = "revoked", "Revoked"
        EXPIRED = "expired", "Expired"

    class Scope(models.TextChoices):
        READ  = "read",  "Read Only"
        WRITE = "write", "Read + Write"
        ADMIN = "admin", "Full Admin"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        BusinessAccount,
        on_delete=models.CASCADE,
        related_name="api_keys",
    )
    name = models.CharField(
        max_length=255,
        help_text="Human label e.g. 'Production App Key' or 'CI/CD Pipeline'",
    )
    # Prefix shown in listings — first 8 chars of raw key e.g. "sk_live_"
    prefix = models.CharField(
        max_length=16,
        editable=False,
        help_text="First characters of the raw key for identification",
    )
    # SHA-256 hash of the raw key — never store plaintext
    key_hash = models.CharField(
        max_length=64,
        unique=True,
        editable=False,
        db_index=True,
    )
    scope = models.CharField(
        max_length=10,
        choices=Scope.choices,
        default=Scope.WRITE,
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    expiry_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Optional expiry. Null = never expires.",
    )
    last_used_at = models.DateTimeField(null=True, blank=True)
    # Track which key this was rotated from (for audit trail)
    rotated_from = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="rotated_to",
    )
    # Optional IP allowlist — empty = allow all IPs
    allowed_ips = models.JSONField(
        default=list,
        blank=True,
        help_text="Optional list of allowed IP addresses. Empty = all IPs allowed.",
    )
    request_count = models.PositiveBigIntegerField(
        default=0,
        editable=False,
        help_text="Total number of requests made with this key",
    )

    class Meta:
        db_table = "api_keys"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} [{self.prefix}...] ({self.status})"

    @property
    def is_valid(self) -> bool:
        """Check if this key can be used right now."""
        if self.status != self.Status.ACTIVE:
            return False
        if self.expiry_at and timezone.now() > self.expiry_at:
            # Auto-expire
            APIKey.objects.filter(id=self.id).update(
                status=self.Status.EXPIRED
            )
            return False
        return True

    @classmethod
    def generate(cls) -> tuple[str, str, str]:
        """
        Generate a new raw key + its hash + prefix.
        Returns (raw_key, key_hash, prefix).

        Raw key format: sk_live_<32 random hex chars>
        This is shown to the user ONCE and never stored.
        """
        import secrets
        raw_key  = f"sk_live_{secrets.token_hex(32)}"
        prefix   = raw_key[:12]
        key_hash = cls._hash_key(raw_key)
        return raw_key, key_hash, prefix

    @classmethod
    def _hash_key(cls, raw_key: str) -> str:
        """SHA-256 hash of the raw key."""
        import hashlib
        return hashlib.sha256(raw_key.encode()).hexdigest()

    @classmethod
    def authenticate(cls, raw_key: str) -> "APIKey | None":
        """
        Look up and validate a raw key.
        Returns the APIKey if valid, None otherwise.
        Updates last_used_at + request_count on success.
        """
        key_hash = cls._hash_key(raw_key)
        try:
            api_key = cls.objects.select_related("business").get(
                key_hash=key_hash,
            )
        except cls.DoesNotExist:
            return None

        if not api_key.is_valid:
            return None

        # Update usage stats — use F() for thread safety
        cls.objects.filter(id=api_key.id).update(
            last_used_at=timezone.now(),
            request_count=models.F("request_count") + 1,
        )

        return api_key

    def revoke(self):
        """Immediately revoke this key."""
        self.status = self.Status.REVOKED
        self.save(update_fields=["status", "updated_at"])

    def allows_ip(self, ip: str) -> bool:
        """Check IP allowlist. Empty list = all IPs allowed."""
        if not self.allowed_ips:
            return True
        return ip in self.allowed_ips

    def allows_method(self, http_method: str) -> bool:
        """Check if scope permits this HTTP method."""
        http_method = http_method.upper()
        if self.scope == self.Scope.READ:
            return http_method == "GET"
        if self.scope == self.Scope.WRITE:
            return http_method in ("GET", "POST", "PUT", "PATCH", "DELETE")
        if self.scope == self.Scope.ADMIN:
            return True
        return False      
    
    
# ─── Labels ───────────────────────────────────────────────────────────────────

class Label(TimeStampedModel):
    """
    A coloured tag scoped to a BusinessAccount.
    Multiple labels can be applied to one conversation.

    Example labels: urgent, billing, vip, follow-up, bug, feedback
    """

    # Predefined colour palette — matches common UI design systems
    COLOUR_CHOICES = [
        ("red",    "Red"),
        ("orange", "Orange"),
        ("yellow", "Yellow"),
        ("green",  "Green"),
        ("blue",   "Blue"),
        ("purple", "Purple"),
        ("pink",   "Pink"),
        ("grey",   "Grey"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        BusinessAccount,
        on_delete=models.CASCADE,
        related_name="labels",
    )
    name = models.CharField(
        max_length=50,
        help_text="Label name e.g. 'urgent', 'billing', 'vip'",
    )
    colour = models.CharField(
        max_length=10,
        choices=COLOUR_CHOICES,
        default="blue",
    )
    description = models.CharField(max_length=255, blank=True, default="")
    is_active   = models.BooleanField(default=True)

    class Meta:
        db_table      = "labels"
        unique_together = ("business", "name")
        ordering      = ["name"]

    def __str__(self):
        return f"[{self.colour}] {self.name} @ {self.business.name}"


class ConversationLabel(TimeStampedModel):
    """
    Many-to-many join between Conversation and Label.
    Tracks who applied the label and when.
    """
    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="conversation_labels",
    )
    label = models.ForeignKey(
        Label,
        on_delete=models.CASCADE,
        related_name="conversation_labels",
    )
    applied_by   = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Agent name or API key prefix that applied this label",
    )

    class Meta:
        db_table        = "conversation_labels"
        unique_together = ("conversation", "label")
        ordering        = ["-created_at"]

    def __str__(self):
        return f"{self.label.name} → Conv {self.conversation_id}"


# ─── Agents ───────────────────────────────────────────────────────────────────

class Agent(TimeStampedModel):
    """
    A human support agent who can be assigned conversations.

    Scoped to one BusinessAccount.
    max_conversations = capacity limit for auto-assignment.
    last_assigned_at  = used for round-robin ordering.
    """

    class Status(models.TextChoices):
        ONLINE  = "online",  "Online"    # available for assignment
        AWAY    = "away",    "Away"      # temporarily unavailable
        OFFLINE = "offline", "Offline"  # not available

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        BusinessAccount,
        on_delete=models.CASCADE,
        related_name="agents",
    )
    name         = models.CharField(max_length=255)
    email        = models.EmailField()
    status       = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.ONLINE,
    )
    max_conversations = models.PositiveIntegerField(
        default=10,
        help_text="Maximum open conversations this agent can handle at once",
    )
    last_assigned_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Used for round-robin ordering — least recently assigned goes first",
    )
    # Total lifetime assignment stats
    total_assigned  = models.PositiveIntegerField(default=0, editable=False)
    total_resolved  = models.PositiveIntegerField(default=0, editable=False)

    class Meta:
        db_table        = "agents"
        unique_together = ("business", "email")
        ordering        = ["name"]

    def __str__(self):
        return f"{self.name} <{self.email}> [{self.status}] @ {self.business.name}"

    @property
    def active_conversation_count(self) -> int:
        """Current number of open conversations assigned to this agent."""
        return Conversation.objects.filter(
            business=self.business,
            assigned_to=self.email,
            status=Conversation.Status.OPEN,
        ).count()

    @property
    def is_available(self) -> bool:
        """True if online and under capacity."""
        if self.status != self.Status.ONLINE:
            return False
        return self.active_conversation_count < self.max_conversations

    def increment_assigned(self):
        Agent.objects.filter(id=self.id).update(
            total_assigned=models.F("total_assigned") + 1,
            last_assigned_at=timezone.now(),
        )

    def increment_resolved(self):
        Agent.objects.filter(id=self.id).update(
            total_resolved=models.F("total_resolved") + 1,
        )


class AssignmentLog(TimeStampedModel):
    """
    Immutable audit trail of every assignment event.
    One record per assignment — manual and auto-assigned.
    """

    class AssignmentType(models.TextChoices):
        AUTO   = "auto",   "Auto (Round-Robin)"
        MANUAL = "manual", "Manual"

    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="assignment_logs",
    )
    agent        = models.ForeignKey(
        Agent,
        on_delete=models.SET_NULL,
        null=True,
        related_name="assignment_logs",
    )
    assigned_by  = models.CharField(
        max_length=255,
        blank=True,
        default="system",
        help_text="Who triggered this assignment (agent email or 'system')",
    )
    assignment_type = models.CharField(
        max_length=10,
        choices=AssignmentType.choices,
        default=AssignmentType.AUTO,
    )
    unassigned_at    = models.DateTimeField(null=True, blank=True)
    unassignment_reason = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "assignment_logs"
        ordering = ["-created_at"]

    def __str__(self):
        return (
            f"{self.assignment_type} | {self.agent} → "
            f"Conv {self.conversation_id} @ {self.created_at:%Y-%m-%d %H:%M}"
        )            