import os
from celery import Celery
from celery.utils.log import get_task_logger

# Tell Celery which Django settings module to use
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "whatsapp_api.settings")

app = Celery("whatsapp_api")

# Load config from Django settings — all CELERY_* keys
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks.py in every INSTALLED_APP
app.autodiscover_tasks()

logger = get_task_logger(__name__)


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Sanity-check task — prints request info."""
    logger.info("Debug task executing | request: %r", self.request)