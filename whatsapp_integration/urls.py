from django.urls import path
from . import views

app_name = "whatsapp_integration"

urlpatterns = [
    path("health/", views.health_check, name="health-check"),
]