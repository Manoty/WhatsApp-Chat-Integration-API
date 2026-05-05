from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.utils import timezone


@api_view(["GET"])
def health_check(request):
    """
    Health check endpoint.
    Used by load balancers, uptime monitors, and CI pipelines
    to confirm the service is alive.
    """
    return Response(
        {
            "status": "ok",
            "service": "WhatsApp Chat Integration API",
            "version": "1.0.0",
            "timestamp": timezone.now().isoformat(),
        }
    )