from django.apps import AppConfig


class WhatsappIntegrationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "whatsapp_integration"

    def ready(self):
        import whatsapp_integration.ws.signals  # noqa: F401