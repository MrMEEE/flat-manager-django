from django.apps import AppConfig
import os
import sys


class FlatpakConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.flatpak'
    
    def ready(self):
        """Initialize missing OSTree repositories on startup."""
        # Connect post_migrate so that tasks are (re-)registered after every
        # migration run, regardless of the process type.
        from django.db.models.signals import post_migrate
        post_migrate.connect(self._check_repositories_signal, sender=self)

        # Also attempt task registration on every normal process startup
        # (gunicorn, celery worker, celery beat).  Skip management commands
        # like migrate/makemigrations where the DB may not be fully ready.
        if 'migrate' not in sys.argv and 'makemigrations' not in sys.argv:
            try:
                self._register_periodic_tasks()
            except Exception:
                # DB might not be ready yet; post_migrate will catch it.
                pass
    
    def _check_repositories_signal(self, sender, **kwargs):
        """Signal handler to check repositories after migrations."""
        try:
            self._check_and_init_repositories()
        except Exception as e:
            # Log but don't crash the application
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to check/initialize repositories on startup: {e}")
        try:
            self._register_periodic_tasks()
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to register periodic tasks: {e}")

    def _register_periodic_tasks(self):
        """Ensure our periodic tasks exist in django_celery_beat."""
        import json
        from django_celery_beat.models import PeriodicTask, IntervalSchedule
        from .models import SiteConfig
        config = SiteConfig.get_solo()

        # ── Upstream version check (configurable interval, default 1 h) ──────
        interval_hours = config.upstream_version_check_interval_hours or 1
        hour_schedule, _ = IntervalSchedule.objects.get_or_create(
            every=interval_hours,
            period=IntervalSchedule.HOURS,
        )
        task, created = PeriodicTask.objects.get_or_create(
            name='Check all upstream versions',
            defaults={
                'task': 'apps.flatpak.tasks.check_all_upstream_versions',
                'interval': hour_schedule,
                'args': json.dumps([]),
                'enabled': config.upstream_version_check_interval_hours > 0,
            },
        )
        if not created:
            task.interval = hour_schedule
            task.enabled = config.upstream_version_check_interval_hours > 0
            task.save(update_fields=['interval', 'enabled'])

        # ── Retry stuck pending promotions (configurable, default 1 min) ─────
        retry_minutes = config.promotion_retry_interval_minutes or 1
        retry_schedule, _ = IntervalSchedule.objects.get_or_create(
            every=retry_minutes,
            period=IntervalSchedule.MINUTES,
        )
        promo_task, promo_created = PeriodicTask.objects.get_or_create(
            name='Retry pending promotions',
            defaults={
                'task': 'apps.flatpak.tasks.retry_pending_promotions',
                'interval': retry_schedule,
                'args': json.dumps([]),
                'enabled': config.promotion_retry_interval_minutes > 0,
            },
        )
        if not promo_created:
            promo_task.interval = retry_schedule
            promo_task.enabled = config.promotion_retry_interval_minutes > 0
            promo_task.save(update_fields=['interval', 'enabled'])

    def _check_and_init_repositories(self):
        """Check all repositories and initialize missing OSTree repos."""
        from .models import Repository
        from .utils.ostree import init_ostree_repo, check_ostree_available
        import logging
        
        logger = logging.getLogger(__name__)
        
        # Check if ostree is available
        if not check_ostree_available():
            logger.warning("OSTree not available - skipping repository initialization check")
            return
        
        repositories = Repository.objects.all()
        if not repositories.exists():
            return
        
        initialized_count = 0
        for repo in repositories:
            repo_path = repo.repo_path
            config_path = os.path.join(repo_path, 'config')
            
            # Check if repository exists by looking for the config file
            if not os.path.exists(config_path):
                logger.info(f"Initializing missing OSTree repository: {repo.name}")
                
                result = init_ostree_repo(
                    repo_path,
                    collection_id=repo.collection_id or None,
                    gpg_key=repo.gpg_key
                )
                
                if result['success']:
                    logger.info(f"✓ Successfully initialized repository: {repo.name}")
                    initialized_count += 1
                else:
                    logger.error(f"✗ Failed to initialize repository {repo.name}: {result.get('error', 'Unknown error')}")
        
        if initialized_count > 0:
            logger.info(f"Initialized {initialized_count} missing OSTree repositories on startup")
