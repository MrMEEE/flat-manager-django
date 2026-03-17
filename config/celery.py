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
        'schedule': 30.0,  # Default 30 seconds; overridden at runtime by SiteConfig
        'options': {
            'expires': 20.0,
        }
    },
    'cleanup-failed-builds': {
        'task': 'apps.flatpak.tasks.cleanup_failed_builds',
        'schedule': 3600.0,  # Run every hour
        'options': {
            'expires': 300.0,  # Task expires after 5 minutes if not executed
        }
    },
    'sync-repo-state': {
        'task': 'apps.flatpak.tasks.sync_repo_state',
        'schedule': 300.0,  # Run every 5 minutes to catch external drift
        'options': {
            'expires': 60.0,
        }
    },
}


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')


# ── Worker lifecycle: fail any packages that were mid-flight ──────────────────

IN_PROGRESS_STATUSES = ['building', 'committing', 'committed', 'publishing']


def _fail_stuck_packages(reason: str) -> None:
    """Mark packages stuck in an in-progress state as failed.
    Called on worker startup (handles previous crash) and graceful shutdown.
    """
    try:
        import django
        django.setup()  # no-op if already set up
        from django.utils import timezone
        from apps.flatpak.models import Package, Build

        stuck_ids = list(
            Package.objects.filter(status__in=IN_PROGRESS_STATUSES)
            .values_list('pk', flat=True)
        )
        if not stuck_ids:
            return

        now = timezone.now()
        # Fail the associated in-progress / built builds first
        Build.objects.filter(
            package_id__in=stuck_ids,
            status__in=IN_PROGRESS_STATUSES + ['built'],
        ).update(status='failed', error_message=reason, completed_at=now)

        Package.objects.filter(pk__in=stuck_ids).update(
            status='failed',
            error_message=reason,
        )
        print(f'[flat-manager] Marked {len(stuck_ids)} stuck package(s) as failed: {reason}')
    except Exception as exc:  # pragma: no cover
        print(f'[flat-manager] Warning: could not reset stuck packages: {exc}')


from celery.signals import worker_ready, worker_shutdown  # noqa: E402


@worker_ready.connect
def on_worker_ready(sender, **kwargs):
    """On startup, fail packages that were building when the last worker died."""
    _fail_stuck_packages('Celery worker restarted — build was interrupted')


@worker_shutdown.connect
def on_worker_shutdown(sender, **kwargs):
    """On graceful shutdown, fail packages that are still in progress."""
    _fail_stuck_packages('Celery worker shut down — build was interrupted')
