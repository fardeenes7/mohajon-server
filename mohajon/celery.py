import os
from celery import Celery
from kombu import Exchange, Queue

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mohajon.settings')

app = Celery('mohajon')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Define specific queues (as per the docker-compose architectural requirements)
app.conf.task_queues = (
    Queue('default', Exchange('default'), routing_key='default'),
    Queue('high_priority', Exchange('high_priority'), routing_key='high_priority'),
    Queue('media_processing', Exchange('media_processing'), routing_key='media_processing'),
    Queue('messenger', Exchange('messenger'), routing_key='messenger'),
    Queue('ai_rag', Exchange('ai_rag'), routing_key='ai_rag'),
    Queue('ai_copy', Exchange('ai_copy'), routing_key='ai_copy'),
    Queue('ai_image', Exchange('ai_image'), routing_key='ai_image'),
)

app.conf.task_default_queue = 'default'
app.conf.task_default_exchange = 'default'
app.conf.task_default_routing_key = 'default'

app.conf.task_routes = {
    'messenger.tasks.embed_faq_entry': {'queue': 'ai_rag', 'routing_key': 'ai_rag'},
    'core.tasks.generate_product_copy': {'queue': 'ai_copy', 'routing_key': 'ai_copy'},
    'core.tasks.generate_ad_copy': {'queue': 'ai_copy', 'routing_key': 'ai_copy'},
    'core.tasks.generate_ad_image': {'queue': 'ai_image', 'routing_key': 'ai_image'},
}

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
