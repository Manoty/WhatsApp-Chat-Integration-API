import logging
from datetime import timedelta, date
from django.utils import timezone
from django.db.models import (
    Count, Avg, Sum, Min, Max, F, Q,
    ExpressionWrapper, DurationField,
    FloatField, IntegerField,
)
from django.db.models.functions import (
    TruncDate, TruncHour, TruncWeek, TruncMonth,
    Extract, Coalesce,
)

from ..models import (
    Message, Conversation, WhatsAppContact,
    BusinessAccount, AutoReplyRule, Agent,
    MessageTemplate, TemplateSend, AssignmentLog,
)

logger = logging.getLogger(__name__)


class AnalyticsService:
    """
    Aggregates system data into chart-ready payloads.

    All methods accept:
      business_id  — scope to one tenant
      date_from    — start of period (default: 30 days ago)
      date_to      — end of period   (default: today)

    All methods return plain dicts safe for JSON serialization.
    """

    # ── Period helpers ────────────────────────────────────────────────────────

    def _period(
        self,
        date_from=None,
        date_to=None,
    ) -> tuple:
        now = timezone.now()
        if not date_from:
            date_from = now - timedelta(days=30)
        if not date_to:
            date_to = now
        return date_from, date_to

    def _biz_filter(self, business_id: str) -> dict:
        if business_id:
            return {"conversation__business__id": business_id}
        return {}

    # ── Overview ─────────────────────────────────────────────────────────────

    def overview(self, business_id: str, date_from=None, date_to=None) -> dict:
        """
        High-level KPI snapshot — the top row of any dashboard.
        Returns totals + period-over-period deltas.
        """
        df, dt = self._period(date_from, date_to)
        period_days = (dt - df).days or 1

        # Previous period for comparison
        prev_df = df - timedelta(days=period_days)
        prev_dt = df

        def msg_qs(start, end, **extra):
            return Message.objects.filter(
                created_at__gte=start,
                created_at__lte=end,
                **self._biz_filter(business_id),
                **extra,
            )

        def conv_qs(start, end, **extra):
            return Conversation.objects.filter(
                created_at__gte=start,
                created_at__lte=end,
                **({
                    "business__id": business_id
                } if business_id else {}),
                **extra,
            )

        # Current period
        cur_messages      = msg_qs(df, dt).count()
        cur_inbound       = msg_qs(df, dt, direction="inbound").count()
        cur_outbound      = msg_qs(df, dt, direction="outbound").count()
        cur_conversations = conv_qs(df, dt).count()
        cur_contacts      = WhatsAppContact.objects.filter(
            created_at__gte=df,
            created_at__lte=dt,
            **({
                "business__id": business_id
            } if business_id else {}),
        ).count()

        # Previous period
        prev_messages      = msg_qs(prev_df, prev_dt).count()
        prev_conversations = conv_qs(prev_df, prev_dt).count()

        def delta_pct(current, previous) -> float:
            if not previous:
                return 100.0 if current else 0.0
            return round(((current - previous) / previous) * 100, 1)

        # Auto-reply rate
        auto_replied = msg_qs(df, dt, direction="outbound").filter(
            conversation__messages__direction="inbound"
        ).distinct().count()
        auto_reply_rate = round(
            (auto_replied / cur_inbound * 100) if cur_inbound else 0, 1
        )

        # Avg response time (inbound → first outbound in same conversation)
        avg_response = self._avg_response_time(business_id, df, dt)

        return {
            "period": {
                "from":  df.isoformat(),
                "to":    dt.isoformat(),
                "days":  period_days,
            },
            "messages": {
                "total":          cur_messages,
                "inbound":        cur_inbound,
                "outbound":       cur_outbound,
                "delta_pct":      delta_pct(cur_messages, prev_messages),
            },
            "conversations": {
                "total":      cur_conversations,
                "delta_pct":  delta_pct(cur_conversations, prev_conversations),
                "open":       Conversation.objects.filter(
                    status="open",
                    **({
                        "business__id": business_id
                    } if business_id else {}),
                ).count(),
            },
            "contacts": {
                "total_new":    cur_contacts,
                "total_all":    WhatsAppContact.objects.filter(
                    **({
                        "business__id": business_id
                    } if business_id else {})
                ).count(),
            },
            "response_time": {
                "avg_seconds":  avg_response,
                "avg_human":    self._humanize_seconds(avg_response),
            },
            "auto_reply_rate": auto_reply_rate,
        }

    # ── Message Analytics ─────────────────────────────────────────────────────

    def messages(
        self,
        business_id: str,
        date_from=None,
        date_to=None,
        granularity: str = "day",   # day | hour | week | month
    ) -> dict:
        """
        Message volume over time, split by direction and type.
        Returns chart-ready series data.
        """
        df, dt = self._period(date_from, date_to)

        trunc_fn = {
            "hour":  TruncHour,
            "day":   TruncDate,
            "week":  TruncWeek,
            "month": TruncMonth,
        }.get(granularity, TruncDate)

        base_qs = Message.objects.filter(
            created_at__gte=df,
            created_at__lte=dt,
            **self._biz_filter(business_id),
        )

        # Volume over time
        volume = (
            base_qs
            .annotate(period=trunc_fn("created_at"))
            .values("period", "direction")
            .annotate(count=Count("id"))
            .order_by("period")
        )

        # By message type
        by_type = (
            base_qs
            .values("message_type")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        # By status
        by_status = (
            base_qs
            .filter(direction="outbound")
            .values("status")
            .annotate(count=Count("id"))
        )

        # Peak hours (0-23)
        peak_hours = (
            base_qs
            .filter(direction="inbound")
            .annotate(hour=Extract("created_at", "hour"))
            .values("hour")
            .annotate(count=Count("id"))
            .order_by("hour")
        )

        return {
            "period":      {"from": df.isoformat(), "to": dt.isoformat()},
            "granularity": granularity,
            "volume_series": self._format_volume_series(volume),
            "by_type":     list(by_type),
            "by_status":   list(by_status),
            "peak_hours":  list(peak_hours),
            "totals": {
                "all":      base_qs.count(),
                "inbound":  base_qs.filter(direction="inbound").count(),
                "outbound": base_qs.filter(direction="outbound").count(),
                "failed":   base_qs.filter(status="failed").count(),
            },
        }

    # ── Conversation Analytics ────────────────────────────────────────────────

    def conversations(
        self,
        business_id: str,
        date_from=None,
        date_to=None,
    ) -> dict:
        """
        Conversation open/close rates, resolution times,
        and status breakdown.
        """
        df, dt = self._period(date_from, date_to)

        biz_q = {"business__id": business_id} if business_id else {}

        base_qs = Conversation.objects.filter(
            created_at__gte=df,
            created_at__lte=dt,
            **biz_q,
        )

        # Status breakdown
        by_status = (
            base_qs
            .values("status")
            .annotate(count=Count("id"))
        )

        # Opened per day
        opened_series = (
            base_qs
            .annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(opened=Count("id"))
            .order_by("day")
        )

        # Avg messages per conversation
        avg_msgs = base_qs.annotate(
            msg_count=Count("messages")
        ).aggregate(avg=Avg("msg_count"))["avg"] or 0

        # Resolution time — conversations that were closed
        resolution = self._resolution_time_stats(business_id, df, dt)

        # Conversations with no response (inbound only, no outbound)
        no_response = base_qs.filter(
            messages__direction="inbound"
        ).exclude(
            messages__direction="outbound"
        ).distinct().count()

        return {
            "period":          {"from": df.isoformat(), "to": dt.isoformat()},
            "by_status":       list(by_status),
            "opened_series":   [
                {
                    "day":    r["day"].isoformat() if r["day"] else None,
                    "opened": r["opened"],
                }
                for r in opened_series
            ],
            "avg_messages_per_conversation": round(avg_msgs, 1),
            "no_response_count": no_response,
            "resolution": resolution,
            "totals": {
                "opened": base_qs.count(),
                "open":   Conversation.objects.filter(status="open", **biz_q).count(),
                "closed": Conversation.objects.filter(status="closed", **biz_q).count(),
                "pending":Conversation.objects.filter(status="pending", **biz_q).count(),
            },
        }

    # ── Agent Analytics ───────────────────────────────────────────────────────

    def agents(
        self,
        business_id: str,
        date_from=None,
        date_to=None,
    ) -> dict:
        """
        Agent performance: assignments, resolutions,
        response times, capacity utilisation.
        """
        df, dt = self._period(date_from, date_to)

        biz_q = {"business__id": business_id} if business_id else {}

        agents_qs = Agent.objects.filter(**biz_q)

        agent_stats = []
        for agent in agents_qs:
            # Assignments in period
            assignments = AssignmentLog.objects.filter(
                agent=agent,
                created_at__gte=df,
                created_at__lte=dt,
            ).count()

            # Resolved in period (closed conversations assigned to this agent)
            resolved = Conversation.objects.filter(
                assigned_to=agent.email,
                status="closed",
                updated_at__gte=df,
                updated_at__lte=dt,
                **biz_q,
            ).count()

            agent_stats.append({
                "agent_id":           str(agent.id),
                "name":               agent.name,
                "email":              agent.email,
                "status":             agent.status,
                "assignments_period": assignments,
                "resolutions_period": resolved,
                "active_now":         agent.active_conversation_count,
                "capacity_pct":       round(
                    (agent.active_conversation_count /
                     agent.max_conversations * 100)
                    if agent.max_conversations else 0
                ),
                "total_assigned":     agent.total_assigned,
                "total_resolved":     agent.total_resolved,
                "resolution_rate":    round(
                    (agent.total_resolved / agent.total_assigned * 100)
                    if agent.total_assigned else 0, 1
                ),
            })

        # Sort by assignments desc
        agent_stats.sort(key=lambda x: x["assignments_period"], reverse=True)

        # Assignment distribution over time
        assignment_series = (
            AssignmentLog.objects.filter(
                created_at__gte=df,
                created_at__lte=dt,
                agent__business__id=business_id,
            )
            .annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(count=Count("id"))
            .order_by("day")
        ) if business_id else []

        return {
            "period":            {"from": df.isoformat(), "to": dt.isoformat()},
            "agents":            agent_stats,
            "assignment_series": [
                {
                    "day":   r["day"].isoformat() if r["day"] else None,
                    "count": r["count"],
                }
                for r in assignment_series
            ],
            "totals": {
                "total_agents":   agents_qs.count(),
                "online":         agents_qs.filter(status="online").count(),
                "away":           agents_qs.filter(status="away").count(),
                "offline":        agents_qs.filter(status="offline").count(),
            },
        }

    # ── Contact Analytics ─────────────────────────────────────────────────────

    def contacts(
        self,
        business_id: str,
        date_from=None,
        date_to=None,
    ) -> dict:
        """
        Contact growth over time and opt-in/out rates.
        """
        df, dt = self._period(date_from, date_to)

        biz_q = {"business__id": business_id} if business_id else {}

        base_qs = WhatsAppContact.objects.filter(**biz_q)

        # Growth series
        growth = (
            base_qs
            .filter(created_at__gte=df, created_at__lte=dt)
            .annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(new_contacts=Count("id"))
            .order_by("day")
        )

        # Running total (cumulative)
        total_before = base_qs.filter(created_at__lt=df).count()

        cumulative = []
        running    = total_before
        for r in growth:
            running += r["new_contacts"]
            cumulative.append({
                "day":         r["day"].isoformat() if r["day"] else None,
                "new":         r["new_contacts"],
                "cumulative":  running,
            })

        # Most active contacts (by message count)
        most_active = (
            WhatsAppContact.objects.filter(**biz_q)
            .annotate(
                msg_count=Count("conversations__messages")
            )
            .order_by("-msg_count")
            .values("id", "phone_number", "display_name", "msg_count")
        )[:10]

        return {
            "period":       {"from": df.isoformat(), "to": dt.isoformat()},
            "growth_series": cumulative,
            "most_active":  list(most_active),
            "totals": {
                "total":       base_qs.count(),
                "new_period":  base_qs.filter(
                    created_at__gte=df, created_at__lte=dt
                ).count(),
                "opted_in":    base_qs.filter(is_opted_in=True).count(),
                "opted_out":   base_qs.filter(is_opted_in=False).count(),
                "opt_in_rate": round(
                    base_qs.filter(is_opted_in=True).count() /
                    base_qs.count() * 100
                    if base_qs.count() else 0, 1
                ),
            },
        }

    # ── Auto-Reply Analytics ──────────────────────────────────────────────────

    def auto_replies(
        self,
        business_id: str,
        date_from=None,
        date_to=None,
    ) -> dict:
        """
        Auto-reply rule performance: trigger counts,
        match rates, top rules, uncovered messages.
        """
        df, dt = self._period(date_from, date_to)

        biz_q = {"business__id": business_id} if business_id else {}

        rules = AutoReplyRule.objects.filter(**biz_q).order_by("-trigger_count")

        total_inbound = Message.objects.filter(
            direction="inbound",
            created_at__gte=df,
            created_at__lte=dt,
            **self._biz_filter(business_id),
        ).count()

        total_triggers = sum(r.trigger_count for r in rules)

        match_rate = round(
            (total_triggers / total_inbound * 100) if total_inbound else 0, 1
        )

        rule_stats = [
            {
                "rule_id":      str(r.id),
                "name":         r.name,
                "keyword":      r.keyword,
                "match_type":   r.match_type,
                "is_fallback":  r.is_fallback,
                "is_active":    r.is_active,
                "trigger_count":r.trigger_count,
                "share_pct":    round(
                    (r.trigger_count / total_triggers * 100)
                    if total_triggers else 0, 1
                ),
            }
            for r in rules
        ]

        return {
            "period":         {"from": df.isoformat(), "to": dt.isoformat()},
            "rules":          rule_stats,
            "totals": {
                "total_inbound":  total_inbound,
                "total_triggers": total_triggers,
                "match_rate_pct": match_rate,
                "unmatched_pct":  round(100 - match_rate, 1),
                "active_rules":   rules.filter(is_active=True).count(),
                "total_rules":    rules.count(),
            },
        }

    # ── Template Analytics ────────────────────────────────────────────────────

    def templates(
        self,
        business_id: str,
        date_from=None,
        date_to=None,
    ) -> dict:
        """
        Template send performance: delivery rates,
        top templates, failure analysis.
        """
        df, dt = self._period(date_from, date_to)

        biz_q = {
            "template__business__id": business_id
        } if business_id else {}

        sends_qs = TemplateSend.objects.filter(
            created_at__gte=df,
            created_at__lte=dt,
            **biz_q,
        )

        total    = sends_qs.count()
        sent     = sends_qs.filter(status="sent").count()
        delivered = sends_qs.filter(status="delivered").count()
        read     = sends_qs.filter(status="read").count()
        failed   = sends_qs.filter(status="failed").count()

        # Per-template breakdown
        by_template = (
            sends_qs
            .values(
                "template__id",
                "template__name",
                "template__template_name",
                "template__category",
            )
            .annotate(
                sends=Count("id"),
                successes=Count("id", filter=Q(status__in=["sent","delivered","read"])),
                failures=Count("id", filter=Q(status="failed")),
            )
            .order_by("-sends")
        )

        # Send volume over time
        send_series = (
            sends_qs
            .annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(count=Count("id"))
            .order_by("day")
        )

        return {
            "period":    {"from": df.isoformat(), "to": dt.isoformat()},
            "by_template": list(by_template),
            "send_series": [
                {
                    "day":   r["day"].isoformat() if r["day"] else None,
                    "count": r["count"],
                }
                for r in send_series
            ],
            "totals": {
                "total":          total,
                "sent":           sent,
                "delivered":      delivered,
                "read":           read,
                "failed":         failed,
                "delivery_rate":  round(delivered / total * 100 if total else 0, 1),
                "read_rate":      round(read / total * 100 if total else 0, 1),
                "failure_rate":   round(failed / total * 100 if total else 0, 1),
            },
        }

    # ── Response Time ─────────────────────────────────────────────────────────

    def response_time(
        self,
        business_id: str,
        date_from=None,
        date_to=None,
    ) -> dict:
        """
        Detailed response time breakdown:
        avg, median bucket, by hour of day, by agent.
        """
        df, dt = self._period(date_from, date_to)

        avg_secs = self._avg_response_time(business_id, df, dt)

        # Distribution buckets
        biz_filter = self._biz_filter(business_id)
        inbound_msgs = Message.objects.filter(
            direction="inbound",
            created_at__gte=df,
            created_at__lte=dt,
            **biz_filter,
        ).select_related("conversation")

        buckets = {
            "under_1_min":   0,
            "1_to_5_min":    0,
            "5_to_30_min":   0,
            "30_to_60_min":  0,
            "over_60_min":   0,
            "no_response":   0,
        }

        for msg in inbound_msgs:
            first_reply = Message.objects.filter(
                conversation=msg.conversation,
                direction="outbound",
                created_at__gt=msg.created_at,
            ).order_by("created_at").first()

            if not first_reply:
                buckets["no_response"] += 1
                continue

            diff_secs = (
                first_reply.created_at - msg.created_at
            ).total_seconds()

            if diff_secs < 60:
                buckets["under_1_min"] += 1
            elif diff_secs < 300:
                buckets["1_to_5_min"] += 1
            elif diff_secs < 1800:
                buckets["5_to_30_min"] += 1
            elif diff_secs < 3600:
                buckets["30_to_60_min"] += 1
            else:
                buckets["over_60_min"] += 1

        return {
            "period":          {"from": df.isoformat(), "to": dt.isoformat()},
            "avg_seconds":     avg_secs,
            "avg_human":       self._humanize_seconds(avg_secs),
            "distribution":    buckets,
        }

    # ── Private Helpers ───────────────────────────────────────────────────────

    def _avg_response_time(
        self, business_id: str, df, dt
    ) -> float:
        """
        Calculate average response time in seconds.
        For each inbound message, find the first outbound reply
        in the same conversation and measure the gap.
        """
        biz_filter = self._biz_filter(business_id)
        inbound = Message.objects.filter(
            direction="inbound",
            created_at__gte=df,
            created_at__lte=dt,
            **biz_filter,
        ).select_related("conversation")[:500]   # cap at 500 for performance

        total_secs = 0
        count      = 0

        for msg in inbound:
            first_reply = Message.objects.filter(
                conversation=msg.conversation,
                direction="outbound",
                created_at__gt=msg.created_at,
            ).order_by("created_at").values("created_at").first()

            if first_reply:
                diff = (
                    first_reply["created_at"] - msg.created_at
                ).total_seconds()
                if diff > 0:
                    total_secs += diff
                    count      += 1

        return round(total_secs / count, 1) if count else 0.0

    def _resolution_time_stats(
        self, business_id: str, df, dt
    ) -> dict:
        """Average time from conversation open to close."""
        biz_q = {"business__id": business_id} if business_id else {}

        closed = Conversation.objects.filter(
            status="closed",
            updated_at__gte=df,
            updated_at__lte=dt,
            **biz_q,
        )

        total_secs = 0
        count      = 0

        for conv in closed[:200]:
            diff = (conv.updated_at - conv.created_at).total_seconds()
            if diff > 0:
                total_secs += diff
                count      += 1

        avg_secs = round(total_secs / count, 1) if count else 0.0

        return {
            "avg_seconds": avg_secs,
            "avg_human":   self._humanize_seconds(avg_secs),
            "sample_size": count,
        }

    def _format_volume_series(self, volume_qs) -> list:
        """
        Reshape direction-split queryset into paired series:
        [
            {"period": "2026-05-01", "inbound": 12, "outbound": 8},
            ...
        ]
        """
        result = {}
        for row in volume_qs:
            key = str(row["period"]) if row["period"] else "unknown"
            if key not in result:
                result[key] = {"period": key, "inbound": 0, "outbound": 0}
            result[key][row["direction"]] = row["count"]
        return list(result.values())

    def _humanize_seconds(self, seconds: float) -> str:
        """Convert seconds to human-readable string."""
        if not seconds:
            return "0s"
        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}s"
        if seconds < 3600:
            mins = seconds // 60
            secs = seconds % 60
            return f"{mins}m {secs}s"
        hours = seconds // 3600
        mins  = (seconds % 3600) // 60
        return f"{hours}h {mins}m"