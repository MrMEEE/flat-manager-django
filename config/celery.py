"""
Celery configuration for flat-manager project.
"""
import os
from celery import Celery
from celery.schedules import crontab
from kombu import Queue

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('flatmanager')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

# ── Queue definitions ─────────────────────────────────────────────────────────
# 'build'  — long-running, CPU/IO-intensive flatpak and BuildStream builds
# 'ops'    — everything else: commit, publish, promote, periodic beat tasks
app.conf.task_queues = (
    Queue('build'),
    Queue('ops'),
)
# Tasks without an explicit queue declaration land on 'ops'.
app.conf.task_default_queue = 'ops'

# Celery Beat schedule for periodic tasks
app.conf.beat_schedule = {
    'check-pending-builds': {
        'task': 'apps.flatpak.tasks.check_pending_builds',
        'schedule': 5.0,  # Run every 5 seconds
        'options': {
            'expires': 3.0,  # Task expires after 3 seconds if not executed
            'queue': 'ops',
        }
    },
    'cleanup-stale-builds': {
        'task': 'apps.flatpak.tasks.cleanup_stale_builds',
        'schedule': 30.0,  # Default 30 seconds; overridden at runtime by SiteConfig
        'options': {
            'expires': 20.0,
            'queue': 'ops',
        }
    },
    'cleanup-failed-builds': {
        'task': 'apps.flatpak.tasks.cleanup_failed_builds',
        'schedule': 3600.0,  # Run every hour
        'options': {
            'expires': 300.0,  # Task expires after 5 minutes if not executed
            'queue': 'ops',
        }
    },
    'cleanup-failed-rpm-builds': {
        'task': 'rpm.cleanup_failed_rpm_builds',
        'schedule': 3600.0,  # Run every hour
        'options': {
            'expires': 300.0,
            'queue': 'ops',
        }
    },
    'sync-repo-state': {
        'task': 'apps.flatpak.tasks.sync_repo_state',
        'schedule': 300.0,  # Run every 5 minutes to catch external drift
        'options': {
            'expires': 60.0,
            'queue': 'ops',
        }
    },
    'check-external-ref-updates': {
        'task': 'apps.flatpak.tasks.check_external_ref_updates',
        'schedule': 21600.0,  # Default 6 hours; overridden at runtime by SiteConfig
        'options': {
            'expires': 3600.0,
            'queue': 'ops',
        }
    },
    'evaluate-dependency-staleness': {
        'task': 'apps.flatpak.tasks.evaluate_dependency_staleness',
        'schedule': 21600.0,  # Default 6 hours; overridden at runtime by SiteConfig
        'options': {
            'expires': 3600.0,
            'queue': 'ops',
        }
    },
}


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')


# ── Worker lifecycle: fail any packages that were mid-flight ──────────────────

IN_PROGRESS_STATUSES = ['building', 'committing', 'committed', 'publishing']


def _fail_stuck_packages(reason: str) -> None:
    """Mark packages/BST sources stuck in an in-progress state as failed.
    Called on worker startup (handles previous crash) and graceful shutdown.
    """
    try:
        import django
        django.setup()  # no-op if already set up
        from django.utils import timezone
        from apps.flatpak.models import Package, Build, BuildStreamSource

        now = timezone.now()

        # --- Flatpak packages ---
        stuck_ids = list(
            Package.objects.filter(status__in=IN_PROGRESS_STATUSES)
            .values_list('pk', flat=True)
        )
        if stuck_ids:
            Build.objects.filter(
                package_id__in=stuck_ids,
                status__in=IN_PROGRESS_STATUSES + ['built'],
            ).update(status='failed', error_message=reason, completed_at=now)
            Package.objects.filter(pk__in=stuck_ids).update(
                status='failed',
                error_message=reason,
            )
            print(f'[flat-manager] Marked {len(stuck_ids)} stuck package(s) as failed: {reason}')

        # --- BuildStream sources ---
        stuck_bst_ids = list(
            BuildStreamSource.objects.filter(status__in=IN_PROGRESS_STATUSES)
            .values_list('pk', flat=True)
        )
        if stuck_bst_ids:
            Build.objects.filter(
                bst_source_id__in=stuck_bst_ids,
                status__in=IN_PROGRESS_STATUSES + ['built'],
            ).update(status='failed', error_message=reason, completed_at=now)
            BuildStreamSource.objects.filter(pk__in=stuck_bst_ids).update(
                status='failed',
                error_message=reason,
            )
            print(f'[flat-manager] Marked {len(stuck_bst_ids)} stuck BST source(s) as failed: {reason}')

    except Exception as exc:  # pragma: no cover
        print(f'[flat-manager] Warning: could not reset stuck packages: {exc}')


from celery.signals import worker_ready, worker_shutdown  # noqa: E402


@worker_ready.connect
def on_worker_ready(sender, **kwargs):
    """On startup, fail packages that were building when the last worker died.
    Only runs on build workers (FLAT_MANAGER_WORKER_TYPE=build).
    """
    if os.environ.get('FLAT_MANAGER_WORKER_TYPE') == 'build':
        _fail_stuck_packages('Celery worker restarted — build was interrupted')


@worker_shutdown.connect
def on_worker_shutdown(sender, **kwargs):
    """On graceful shutdown, fail packages that are still in progress.
    Only runs on build workers (FLAT_MANAGER_WORKER_TYPE=build).
    """
    if os.environ.get('FLAT_MANAGER_WORKER_TYPE') == 'build':
        _fail_stuck_packages('Celery worker shut down — build was interrupted')
