"""
Celery configuration for flat-manager project.
"""
import os
from celery import Celery
from celery.schedules import crontab

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('flatmanager')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

# Celery Beat schedule for periodic tasks
app.conf.beat_schedule = {
    'check-pending-builds': {
        'task': 'apps.flatpak.tasks.check_pending_builds',
        'schedule': 5.0,  # Run every 5 seconds
        'options': {
            'expires': 3.0,  # Task expires after 3 seconds if not executed
        }
    },
    'cleanup-stale-builds': {
        'task': 'apps.flatpak.tasks.cleanup_stale_builds',
        'schedule': 300.0,  # Run every 5 minutes
        'options': {
            'expires': 60.0,  # Task expires after 1 minute if not executed
        }
    },
    'cleanup-failed-builds': {
        'task': 'apps.flatpak.tasks.cleanup_failed_builds',
        'schedule': 3600.0,  # Run every hour
        'options': {
            'expires': 300.0,  # Task expires after 5 minutes if not executed
        }
    },
}


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
