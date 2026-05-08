import logging
from ..models import Label, ConversationLabel, Conversation

logger = logging.getLogger(__name__)


class LabelService:
    """
    Manages applying, removing, and querying labels on conversations.
    """

    def apply_labels(
        self,
        conversation: Conversation,
        label_names: list[str],
        applied_by: str = "",
    ) -> list[ConversationLabel]:
        """
        Apply a list of label names to a conversation.
        Creates labels that don't exist yet (auto-create).
        Returns list of ConversationLabel records.
        Skips duplicates silently.
        """
        business = conversation.business
        applied  = []

        for name in label_names:
            name = name.strip().lower()
            if not name:
                continue

            # Auto-create label if it doesn't exist
            label, created = Label.objects.get_or_create(
                business=business,
                name=name,
                defaults={"colour": "blue", "is_active": True},
            )
            if created:
                logger.info(
                    "Label auto-created | name=%s | business=%s",
                    name, business.name,
                )

            conv_label, new = ConversationLabel.objects.get_or_create(
                conversation=conversation,
                label=label,
                defaults={"applied_by": applied_by},
            )
            if new:
                applied.append(conv_label)
                logger.info(
                    "Label applied | label=%s | conversation=%s | by=%s",
                    name, conversation.id, applied_by,
                )

        return applied

    def remove_labels(
        self,
        conversation: Conversation,
        label_names: list[str],
    ) -> int:
        """
        Remove labels from a conversation by name.
        Returns count of labels removed.
        """
        removed = ConversationLabel.objects.filter(
            conversation=conversation,
            label__name__in=[n.lower() for n in label_names],
        ).delete()

        count = removed[0]
        logger.info(
            "Labels removed | count=%d | conversation=%s",
            count, conversation.id,
        )
        return count

    def set_labels(
        self,
        conversation: Conversation,
        label_names: list[str],
        applied_by: str = "",
    ) -> list[str]:
        """
        Replace ALL labels on a conversation with the given set.
        Removes labels not in the new list, adds missing ones.
        Returns the final list of label names.
        """
        # Remove all current labels
        ConversationLabel.objects.filter(
            conversation=conversation
        ).delete()

        # Apply new set
        self.apply_labels(conversation, label_names, applied_by)

        return label_names

    def get_labels(self, conversation: Conversation) -> list[dict]:
        """Return all labels on a conversation as dicts."""
        return list(
            ConversationLabel.objects.filter(
                conversation=conversation
            ).select_related("label").values(
                "label__id",
                "label__name",
                "label__colour",
                "applied_by",
                "created_at",
            )
        )