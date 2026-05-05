from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("whatsapp_integration.urls", namespace="whatsapp_integration")),
]