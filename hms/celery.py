import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
import os
from celery import Celery
import kombu.transport.redis  # force Redis transport registration

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hms.settings")

app = Celery("hms")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.broker_url = 'redis://localhost:6379/0'
app.conf.result_backend = 'redis://localhost:6379/0'
app.conf.broker_connection_retry_on_startup = True
app.conf.task_always_eager = False

# Pre-establish connection before async loop starts
# from kombu import Connection
# with Connection('redis://localhost:6379/0') as conn:
#     conn.ensure_connection(max_retries=1)

@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f"Request: {self.request!r}")