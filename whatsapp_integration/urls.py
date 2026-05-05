from django.urls import path
from . import views

app_name = "whatsapp_integration"

urlpatterns = [
    path("health/", views.health_check, name="health-check"),
    path("stats/", views.system_stats, name="system-stats"),
    path("webhook/whatsapp/", views.webhook_receiver, name="webhook-receiver"),
]