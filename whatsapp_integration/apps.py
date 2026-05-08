from django.apps import AppConfig


class WhatsappIntegrationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name               = "whatsapp_integration"

    def ready(self):
        """Import signals so they register with Django's signal dispatcher."""
        import whatsapp_integration.signals  # noqa: F401