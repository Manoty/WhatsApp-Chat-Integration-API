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