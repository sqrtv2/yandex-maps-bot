"""
Celery application configuration for background tasks.
"""
from celery import Celery, signals
from celery.schedules import crontab
import logging
import os

from app.config import settings

logger = logging.getLogger(__name__)

# Create Celery instance
celery_app = Celery("yandex_maps_bot")

# Configure Celery
celery_app.conf.update(
    broker_url=settings.redis_url,
    result_backend=settings.redis_url,

    # Serialization
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',

    # Timezone
    timezone='UTC',
    enable_utc=True,

    # Task routing — each task type gets its own dedicated queue
    # Schedulers & watchdogs go to 'yandex_search' — search worker always has free slots when scheduler needs to run
    task_routes={
        'tasks.yandex_search.schedule_search_visits': {'queue': 'yandex_search'},
        'tasks.yandex_search.daily_search_stats_reset': {'queue': 'yandex_search'},
        'tasks.yandex_scheduler.queue_watchdog': {'queue': 'yandex_search'},
        # Maps scheduler/maintenance run fast (<1s) — route to dedicated
        # 'maps_scheduler' queue that maps worker also listens on, so they
        # execute immediately without competing with warmup/search queues.
        'tasks.yandex_maps.schedule_visits': {'queue': 'maps_scheduler'},
        'tasks.yandex_maps.daily_stats_reset': {'queue': 'maps_scheduler'},
        'tasks.yandex_maps.cleanup_used_profiles': {'queue': 'maps_scheduler'},
        # Warmup schedulers/maintenance go to 'default' so they don't get stuck
        # behind thousands of warmup_profile_task entries
        'tasks.warmup.auto_schedule_initial_warmup': {'queue': 'default'},
        'tasks.warmup.auto_maintain_profile_pool': {'queue': 'default'},
        'tasks.warmup.periodic_rewarmup': {'queue': 'default'},
        'tasks.warmup.auto_fix_stuck_processes': {'queue': 'default'},
        'tasks.warmup.cleanup_orphaned_chrome_processes': {'queue': 'default'},
        'tasks.warmup.warmup_watchdog': {'queue': 'default'},
        'tasks.warmup.generate_warmup_sites_task': {'queue': 'default'},
        'tasks.warmup.*': {'queue': 'warmup'},
        # Camoufox warmup runs on its own dedicated worker (separate browser
        # binary, can't share sync_playwright loop with chromium workers).
        'tasks.warmup_camoufox.*': {'queue': 'warmup_camoufox'},
        'tasks.yandex_maps.*': {'queue': 'yandex_maps'},
        'tasks.yandex_search.*': {'queue': 'yandex_search'},
        'tasks.proxy.*': {'queue': 'proxy'},
        'tasks.maintenance.*': {'queue': 'maintenance'},
        'tasks.parser.*': {'queue': 'parser'},
        'tasks.drop_domains.*': {'queue': 'drop_domains'},
    },

    # Worker settings
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    # NOTE: worker_max_tasks_per_child is set per-worker via CLI --max-tasks-per-child
    # in docker-compose.yml (warmup=50, search=2) — do NOT set globally here.

    # Task settings
    task_soft_time_limit=1800,  # 30 minutes
    task_time_limit=2100,       # 35 minutes
    task_default_queue='default',
    task_default_exchange='default',
    task_default_exchange_type='direct',
    task_default_routing_key='default',

    # Result settings
    result_expires=3600,  # 1 hour

    # Error handling
    task_reject_on_worker_lost=True,
    task_ignore_result=False,

    # Monitoring
    worker_send_task_events=True,
    task_send_sent_event=True,

    # Beat schedule for periodic tasks
    beat_schedule={
        'proxy-health-check': {
            'task': 'tasks.maintenance.check_all_proxies',
            'schedule': crontab(minute='*/15'),
        },
        'cleanup-old-tasks': {
            'task': 'tasks.maintenance.cleanup_old_tasks',
            'schedule': crontab(minute=0, hour=2),
        },
        'update-proxy-stats': {
            'task': 'tasks.maintenance.update_proxy_statistics',
            'schedule': crontab(minute='*/30'),
        },
        'profile-maintenance': {
            'task': 'tasks.maintenance.profile_maintenance',
            'schedule': crontab(minute=0, hour=1),
        },
        'yandex-maps-scheduler': {
            'task': 'tasks.yandex_maps.schedule_visits',
            'schedule': crontab(minute='*/5'),
        },
        'yandex-daily-stats-reset': {
            'task': 'tasks.yandex_maps.daily_stats_reset',
            'schedule': crontab(minute=0, hour=0),
        },
        'yandex-cleanup-used-profiles': {
            'task': 'tasks.yandex_maps.cleanup_used_profiles',
            'schedule': crontab(minute='*/30'),
        },
        'process-health-check': {
            'task': 'tasks.warmup.auto_fix_stuck_processes',
            'schedule': crontab(minute='*/10'),
        },
        'periodic-rewarmup': {
            'task': 'tasks.warmup.periodic_rewarmup',
            'schedule': crontab(minute='*/15'),
        },
        'yandex-search-scheduler': {
            'task': 'tasks.yandex_search.schedule_search_visits',
            'schedule': crontab(minute='*/1'),
        },
        'yandex-search-daily-stats-reset': {
            'task': 'tasks.yandex_search.daily_search_stats_reset',
            'schedule': crontab(minute=0, hour=0),
        },
        'queue-watchdog': {
            'task': 'tasks.yandex_scheduler.queue_watchdog',
            'schedule': crontab(minute='*/3'),
        },
        'auto-initial-warmup': {
            'task': 'tasks.warmup.auto_schedule_initial_warmup',
            'schedule': crontab(minute='*/5'),
        },
        'cleanup-orphaned-chrome': {
            'task': 'tasks.warmup.cleanup_orphaned_chrome_processes',
            'schedule': crontab(minute='*/2'),
        },
        'auto-maintain-profile-pool': {
            'task': 'tasks.warmup.auto_maintain_profile_pool',
            'schedule': crontab(minute='*/5'),
        },
        'warmup-watchdog': {
            'task': 'tasks.warmup.warmup_watchdog',
            'schedule': crontab(minute='*/3'),
        },
    }
)

# Auto-discover tasks
celery_app.autodiscover_tasks([
    'tasks.warmup',
    'tasks.warmup_camoufox',
    'tasks.yandex_maps',
    'tasks.yandex_scheduler',
    'tasks.yandex_search',
    'tasks.proxy',
    'tasks.maintenance',
    'tasks.drop_domains',
    'parser',
    'mailing',
])


# Reap zombie Chrome processes periodically and on worker shutdown.
# Chrome spawns many sub-processes; if the parent dies (e.g. Celery SIGKILL
# from time_limit), children become orphans and eventually zombies.
def _reap_zombie_chrome():
    """Kill orphaned Chrome/chromedriver processes and reap zombies."""
    import subprocess, os
    killed = 0
    try:
        # Reap any zombie children of this process
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
                if pid == 0:
                    break
                killed += 1
            except ChildProcessError:
                break
    except Exception:
        pass

    try:
        from core.browser_manager import cleanup_orphaned_chrome
        killed += cleanup_orphaned_chrome()
    except Exception:
        pass

    if killed:
        logger.info(f"🧹 Reaped {killed} zombie/orphaned Chrome processes")
    return killed


@signals.worker_process_init.connect
def _reseed_random_on_fork(**kwargs):
    """Re-seed random on fork. Do NOT kill Chrome here — other workers may be using them!"""
    import random
    import os
    random.seed(os.urandom(32))
    logger.info("🎲 Random re-seeded in forked worker (pid=%d)", os.getpid())

    # Dispose inherited DB connections — forked processes must NOT share parent's pool
    try:
        from app.database import engine
        engine.dispose()
    except Exception as e:
        logger.warning("⚠️ Failed to dispose DB engine on fork: %s", e)

    # Only reap zombie children of THIS process (safe, targeted)
    try:
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
                if pid == 0:
                    break
            except ChildProcessError:
                break
    except Exception:
        pass

    # NOTE: Do NOT clean SingletonLock files here — other workers may have active browsers.
    # Lock files are cleaned per-profile in BrowserManager.create_browser_session() instead.

    # Clean up orphaned Chrome/node-driver from dead workers.
    # This is SAFE: only kills processes whose parent worker is no longer alive.
    try:
        from core.browser_manager import cleanup_orphaned_chrome
        cleanup_orphaned_chrome()
    except Exception as e:
        logger.warning("⚠️ Failed to cleanup orphaned chrome on fork: %s", e)


@signals.worker_process_shutdown.connect
def _cleanup_playwright_on_process_exit(**kwargs):
    """Clean up Playwright instance when this worker process is being replaced.
    
    Called when max-tasks-per-child triggers. Properly closes this process's
    Playwright Node.js driver and its Chrome children without affecting other workers.
    """
    import os
    logger.info("🔄 Worker process %d shutting down — cleaning up Playwright...", os.getpid())
    try:
        from core.browser_manager import _playwright_instance
        if _playwright_instance is not None:
            try:
                _playwright_instance.stop()
            except Exception:
                pass
    except Exception:
        pass


@signals.worker_shutdown.connect
def _cleanup_chrome_on_worker_shutdown(**kwargs):
    """Kill all Chrome processes when the entire Celery worker shuts down.
    Safe here because ALL worker processes are shutting down."""
    logger.info("🛑 Worker shutting down — cleaning up ALL Chrome processes...")
    try:
        from core.browser_manager import cleanup_all_chrome
        cleanup_all_chrome()
    except Exception:
        pass


@signals.task_postrun.connect
def _reap_zombies_after_task(**kwargs):
    """Reap zombie children after every task completes.
    SAFE: Only reaps zombies of THIS process, does NOT kill other workers' Chrome."""
    import os
    try:
        while True:
            pid, _ = os.waitpid(-1, os.WNOHANG)
            if pid == 0:
                break
    except ChildProcessError:
        pass
    except Exception:
        pass
    # Clean up orphaned Chrome after every task completion.
    # Uses the SAFE version that only kills Chrome with no live parent worker.
    try:
        from core.browser_manager import cleanup_orphaned_chrome
        cleanup_orphaned_chrome()
    except Exception:
        pass


@signals.task_failure.connect
def _cleanup_chrome_after_task_failure(**kwargs):
    """Clean up truly orphaned Chrome after task failure.
    
    Uses the SAFE orphan detection (checks parent chain) instead of
    killing by age, since active tasks can run up to 18 minutes.
    """
    try:
        from core.browser_manager import cleanup_orphaned_chrome
        cleanup_orphaned_chrome()
    except Exception:
        pass


# Task failure callback
@celery_app.task(bind=True)
def debug_task(self):
    """Debug task for testing Celery configuration."""
    print(f'Request: {self.request!r}')
    return "Debug task completed successfully"


# Custom task base class
class BaseTask(celery_app.Task):
    """Base task class with common functionality."""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """Called when task fails."""
        logger.error(f"Task {task_id} failed: {exc}")

        # Update task status in database
        try:
            from app.database import get_db_session
            from app.models.task import Task

            with get_db_session() as db:
                task_obj = db.query(Task).filter(Task.celery_task_id == task_id).first()
                if task_obj:
                    task_obj.fail_with_error(str(exc))
                    db.commit()
        except Exception as e:
            logger.error(f"Error updating task failure status: {e}")

    def on_success(self, retval, task_id, args, kwargs):
        """Called when task succeeds."""
        # Check if the task returned an error status (caught exception, returned dict with status='error')
        is_logical_error = isinstance(retval, dict) and retval.get('status') in ('error', 'not_found', 'click_failed')

        if is_logical_error:
            logger.info(f"Task {task_id} finished with logical error: {retval.get('error', retval.get('status', 'unknown'))[:120]}")
        else:
            logger.info(f"Task {task_id} completed successfully")

        # Update task status in database
        try:
            from app.database import get_db_session
            from app.models.task import Task

            with get_db_session() as db:
                task_obj = db.query(Task).filter(Task.celery_task_id == task_id).first()
                if task_obj:
                    if is_logical_error:
                        # Task caught its own error — keep the 'failed' status that was already set
                        # by _update_search_task_log. Only update if still in_progress (safety net).
                        if task_obj.status == 'in_progress':
                            error_msg = str(retval.get('error', 'Unknown error'))[:500]
                            task_obj.fail_with_error(error_msg)
                            db.commit()
                        # Otherwise don't overwrite — the task already set the correct status
                    else:
                        result = retval if isinstance(retval, dict) else {"result": retval}
                        task_obj.complete_successfully(result)
                        db.commit()
        except Exception as e:
            logger.error(f"Error updating task success status: {e}")

    def on_retry(self, exc, task_id, args, kwargs, einfo):
        """Called when task is retried."""
        logger.warning(f"Task {task_id} retrying: {exc}")

        # Update task retry status in database
        try:
            from app.database import get_db_session
            from app.models.task import Task

            with get_db_session() as db:
                task_obj = db.query(Task).filter(Task.celery_task_id == task_id).first()
                if task_obj:
                    task_obj.add_log(f"Retry attempt {task_obj.retry_count + 1}: {exc}")
                    db.commit()
        except Exception as e:
            logger.error(f"Error updating task retry status: {e}")


# Add file handler via Celery's signal — preserves Celery's own StreamHandler
# that correctly writes to fd 2 (Docker log).  Using dictConfig() inside
# worker_ready was BREAKING logging: the new StreamHandler captured Celery's
# LoggingProxy as its stream, creating a circular log chain that silently
# dropped all messages in forked replacement workers.
@signals.after_setup_logger.connect
def _add_file_handler(logger, loglevel, logfile, format, colorize, **kwargs):
    """Add RotatingFileHandler to root logger (runs once in main process)."""
    from logging.handlers import RotatingFileHandler
    log_path = os.path.join(settings.logs_dir, 'celery.log')
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    fh = RotatingFileHandler(log_path, maxBytes=10485760, backupCount=5)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        '[%(asctime)s] %(levelname)s [%(name)s:%(lineno)d] %(message)s'
    ))
    logger.addHandler(fh)


# Worker ready signal
@signals.worker_ready.connect
def worker_ready(sender=None, **kwargs):
    """Called when worker is ready."""
    logger.info(f"Worker {sender} is ready")


# Task started signal
@signals.task_prerun.connect
def task_prerun(sender=None, task_id=None, task=None, args=None, kwargs=None, **kwds):
    """Called before task execution."""
    logger.info(f"Task {task_id} started: {task.name}")

    # Update task status in database
    try:
        from app.database import get_db_session
        from app.models.task import Task

        with get_db_session() as db:
            task_obj = db.query(Task).filter(Task.celery_task_id == task_id).first()
            if task_obj:
                task_obj.start_execution(worker_id=str(sender), celery_task_id=task_id)
                db.commit()
    except Exception as e:
        logger.error(f"Error updating task prerun status: {e}")


# Task completed signal
@signals.task_postrun.connect
def task_postrun(sender=None, task_id=None, task=None, args=None, kwargs=None,
                retval=None, state=None, **kwds):
    """Called after task execution."""
    logger.info(f"Task {task_id} finished with state: {state}")


# Worker shutdown signal
@signals.worker_shutdown.connect
def worker_shutdown(sender=None, **kwargs):
    """Called when worker shuts down."""
    logger.info(f"Worker {sender} is shutting down")


# Utility functions

def get_task_status(task_id: str) -> dict:
    """Get task status from Celery."""
    try:
        result = celery_app.AsyncResult(task_id)
        return {
            'task_id': task_id,
            'status': result.status,
            'result': result.result,
            'traceback': result.traceback,
            'successful': result.successful(),
            'failed': result.failed()
        }
    except Exception as e:
        logger.error(f"Error getting task status for {task_id}: {e}")
        return {
            'task_id': task_id,
            'status': 'ERROR',
            'result': None,
            'traceback': str(e),
            'successful': False,
            'failed': True
        }


def cancel_task(task_id: str) -> bool:
    """Cancel a running task."""
    try:
        celery_app.control.revoke(task_id, terminate=True)
        logger.info(f"Task {task_id} cancelled")
        return True
    except Exception as e:
        logger.error(f"Error cancelling task {task_id}: {e}")
        return False


def get_worker_stats() -> dict:
    """Get statistics about Celery workers."""
    try:
        inspect = celery_app.control.inspect()
        stats = inspect.stats()
        active_tasks = inspect.active()
        scheduled_tasks = inspect.scheduled()

        return {
            'stats': stats or {},
            'active_tasks': active_tasks or {},
            'scheduled_tasks': scheduled_tasks or {},
            'workers_online': len(stats) if stats else 0
        }
    except Exception as e:
        logger.error(f"Error getting worker stats: {e}")
        return {
            'stats': {},
            'active_tasks': {},
            'scheduled_tasks': {},
            'workers_online': 0
        }


def purge_queue(queue_name: str = None) -> dict:
    """Purge all tasks from queue."""
    try:
        if queue_name:
            result = celery_app.control.purge()
        else:
            result = celery_app.control.purge()

        logger.info(f"Purged queue {queue_name or 'all'}")
        return result
    except Exception as e:
        logger.error(f"Error purging queue {queue_name}: {e}")
        return {}


# Make celery app available for CLI
app = celery_app


if __name__ == '__main__':
    celery_app.start()