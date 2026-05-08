import logging
from django.utils import timezone
from ..models import Agent, Conversation, AssignmentLog, BusinessAccount

logger = logging.getLogger(__name__)


class AssignmentEngine:
    """
    Assigns conversations to agents using round-robin ordering.

    Round-robin algorithm:
      1. Filter agents: online + under capacity
      2. Order by last_assigned_at ASC (nulls first)
         → least recently assigned goes next
      3. Assign first agent in that list
      4. Log the assignment

    This guarantees even distribution across all available agents.
    """

    def auto_assign(
        self, conversation: Conversation
    ) -> Agent | None:
        """
        Automatically assign a conversation to the best available agent.
        Returns the Agent if assigned, None if no agents available.
        """
        business = conversation.business

        agent = self._pick_next_agent(business)
        if not agent:
            logger.info(
                "No available agents for auto-assignment | business=%s",
                business.name,
            )
            return None

        self._do_assign(
            conversation=conversation,
            agent=agent,
            assigned_by="system",
            assignment_type=AssignmentLog.AssignmentType.AUTO,
        )

        return agent

    def manual_assign(
        self,
        conversation: Conversation,
        agent: Agent,
        assigned_by: str = "",
    ) -> Agent:
        """
        Manually assign a conversation to a specific agent.
        Overrides any existing assignment.
        """
        self._do_assign(
            conversation=conversation,
            agent=agent,
            assigned_by=assigned_by or "manual",
            assignment_type=AssignmentLog.AssignmentType.MANUAL,
        )
        return agent

    def unassign(
        self,
        conversation: Conversation,
        reason: str = "",
    ) -> bool:
        """
        Remove assignment from a conversation.
        Marks the latest AssignmentLog as unassigned.
        """
        if not conversation.assigned_to:
            return False

        # Close the latest assignment log
        latest = AssignmentLog.objects.filter(
            conversation=conversation,
            unassigned_at__isnull=True,
        ).order_by("-created_at").first()

        if latest:
            latest.unassigned_at       = timezone.now()
            latest.unassignment_reason = reason
            latest.save(update_fields=[
                "unassigned_at", "unassignment_reason", "updated_at",
            ])

        # Clear assignment on conversation
        conversation.assigned_to = ""
        conversation.save(update_fields=["assigned_to", "updated_at"])

        logger.info(
            "Conversation unassigned | conv=%s | reason=%s",
            conversation.id, reason,
        )
        return True

    def get_agent_workload(self, business: BusinessAccount) -> list[dict]:
        """
        Return workload stats for all agents in a business.
        Useful for dashboard display.
        """
        agents = Agent.objects.filter(business=business)
        result = []

        for agent in agents:
            result.append({
                "agent_id":          str(agent.id),
                "name":              agent.name,
                "email":             agent.email,
                "status":            agent.status,
                "active_conversations": agent.active_conversation_count,
                "max_conversations": agent.max_conversations,
                "capacity_pct":      round(
                    (agent.active_conversation_count / agent.max_conversations) * 100
                    if agent.max_conversations else 0
                ),
                "is_available":      agent.is_available,
                "total_assigned":    agent.total_assigned,
                "total_resolved":    agent.total_resolved,
                "last_assigned_at":  agent.last_assigned_at,
            })

        return sorted(result, key=lambda x: x["capacity_pct"])

    # ── Private helpers ───────────────────────────────────────────────────────

    def _pick_next_agent(self, business: BusinessAccount) -> Agent | None:
        """
        Round-robin: find the online agent with the most
        available capacity who was assigned least recently.
        """
        agents = Agent.objects.filter(
            business=business,
            status=Agent.Status.ONLINE,
        ).order_by(
            "last_assigned_at",   # nulls first = never assigned goes first
        )

        for agent in agents:
            if agent.is_available:
                return agent

        return None

    def _do_assign(
        self,
        conversation: Conversation,
        agent: Agent,
        assigned_by: str,
        assignment_type: str,
    ):
        """Perform the assignment and write the audit log."""

        # Update conversation
        conversation.assigned_to = agent.email
        conversation.save(update_fields=["assigned_to", "updated_at"])

        # Write assignment log
        AssignmentLog.objects.create(
            conversation=conversation,
            agent=agent,
            assigned_by=assigned_by,
            assignment_type=assignment_type,
        )

        # Update agent stats
        agent.increment_assigned()

        # Dispatch webhook event
        try:
            from .webhook_dispatcher import WebhookDispatcher
            from .event_builder import EventBuilder

            payload = EventBuilder().build(
                event_type="conversation.assigned",
                business_id=str(conversation.business_id),
                data={
                    "conversation_id":  str(conversation.id),
                    "agent_id":         str(agent.id),
                    "agent_name":       agent.name,
                    "agent_email":      agent.email,
                    "assignment_type":  assignment_type,
                    "assigned_by":      assigned_by,
                    "assigned_at":      timezone.now().isoformat(),
                },
            )
            WebhookDispatcher().dispatch(
                business_id=str(conversation.business_id),
                event_type="conversation.assigned",
                payload=payload,
            )
        except Exception as exc:
            logger.warning(
                "Assignment webhook dispatch failed (non-fatal): %s", exc
            )

        logger.info(
            "Conversation assigned | conv=%s | agent=%s | type=%s | by=%s",
            conversation.id, agent.email, assignment_type, assigned_by,
        )