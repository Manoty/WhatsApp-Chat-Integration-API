import logging
from django.utils import timezone
from ..models import APIKey, BusinessAccount

logger = logging.getLogger(__name__)


class APIKeyService:
    """
    Handles the full API key lifecycle:
      - Creation with scope + expiry + IP allowlist
      - Rotation (new key, old key revoked atomically)
      - Revocation
      - Listing with filtering
      - Cleanup of expired keys
    """

    def create_key(
        self,
        business_id: str,
        name: str,
        scope: str = APIKey.Scope.WRITE,
        expiry_at=None,
        allowed_ips: list = None,
    ) -> tuple[APIKey, str]:
        """
        Create a new API key.
        Returns (APIKey instance, raw_key_string).

        The raw_key is returned ONCE and never stored — caller
        must display it to the user immediately.
        """
        business = self._get_business(business_id)

        raw_key, key_hash, prefix = APIKey.generate()

        api_key = APIKey.objects.create(
            business=business,
            name=name,
            prefix=prefix,
            key_hash=key_hash,
            scope=scope,
            expiry_at=expiry_at,
            allowed_ips=allowed_ips or [],
        )

        logger.info(
            "API key created | id=%s | name=%s | scope=%s | business=%s",
            api_key.id, name, scope, business.name,
        )

        return api_key, raw_key

    def rotate_key(self, key_id: str) -> tuple[APIKey, str]:
        """
        Rotate an existing key:
          1. Generate new key with same config
          2. Revoke the old key
          3. Link new key to old via rotated_from

        Returns (new_APIKey, raw_new_key).
        Old key is immediately revoked — no grace period.
        """
        try:
            old_key = APIKey.objects.get(id=key_id)
        except APIKey.DoesNotExist:
            raise ValueError(f"APIKey not found: {key_id}")

        if old_key.status == APIKey.Status.REVOKED:
            raise ValueError("Cannot rotate a revoked key.")

        raw_key, key_hash, prefix = APIKey.generate()

        new_key = APIKey.objects.create(
            business=old_key.business,
            name=f"{old_key.name} (rotated)",
            prefix=prefix,
            key_hash=key_hash,
            scope=old_key.scope,
            expiry_at=old_key.expiry_at,
            allowed_ips=old_key.allowed_ips,
            rotated_from=old_key,
        )

        old_key.revoke()

        logger.info(
            "API key rotated | old=%s | new=%s | business=%s",
            old_key.id, new_key.id, old_key.business.name,
        )

        return new_key, raw_key

    def revoke_key(self, key_id: str) -> APIKey:
        """Immediately revoke an API key."""
        try:
            api_key = APIKey.objects.get(id=key_id)
        except APIKey.DoesNotExist:
            raise ValueError(f"APIKey not found: {key_id}")

        if api_key.status == APIKey.Status.REVOKED:
            raise ValueError("Key is already revoked.")

        api_key.revoke()

        logger.info(
            "API key revoked | id=%s | name=%s | business=%s",
            api_key.id, api_key.name, api_key.business.name,
        )

        return api_key

    def cleanup_expired(self) -> int:
        """
        Mark all past-expiry ACTIVE keys as EXPIRED.
        Called by a scheduled Celery task.
        Returns count of keys updated.
        """
        updated = APIKey.objects.filter(
            status=APIKey.Status.ACTIVE,
            expiry_at__lt=timezone.now(),
        ).update(status=APIKey.Status.EXPIRED)

        if updated:
            logger.info("Expired %d API keys", updated)

        return updated

    def _get_business(self, business_id: str) -> BusinessAccount:
        try:
            return BusinessAccount.objects.get(id=business_id, is_active=True)
        except BusinessAccount.DoesNotExist:
            raise ValueError(
                f"BusinessAccount not found or inactive: {business_id}"
            )