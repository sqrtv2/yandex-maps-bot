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
    task_routes={
        'tasks.warmup.*': {'queue': 'warmup'},
        'tasks.yandex_maps.*': {'queue': 'yandex_maps'},
        'tasks.yandex_search.*': {'queue': 'yandex_search'},
        'tasks.proxy.*': {'queue': 'proxy'},
        'tasks.maintenance.*': {'queue': 'maintenance'},
        'tasks.parser.*': {'queue': 'parser'},
    },

    # Worker settings
    worker_prefetch_multiplier=1,
    task_acks_late=False,
    worker_max_tasks_per_child=1000,

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
            'schedule': crontab(minute='*/5'),
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
    }
)

# Auto-discover tasks
celery_app.autodiscover_tasks([
    'tasks.warmup',
    'tasks.yandex_maps',
    'tasks.yandex_scheduler',
    'tasks.yandex_search',
    'tasks.proxy',
    'tasks.maintenance'
])


# Pre-patch chromedriver once BEFORE any task is dispatched to workers.
# This avoids the race condition where multiple ForkPoolWorkers try to
# patch the same binary simultaneously.
@signals.worker_init.connect
def _pre_patch_chromedriver_on_worker_init(**kwargs):
    """Eagerly patch chromedriver when the Celery worker process starts."""
    try:
        from core.browser_manager import _ensure_patched_chromedriver
        path = _ensure_patched_chromedriver()
        logger.info(f"🔧 Chromedriver pre-patched at worker init: {path}")
    except Exception as e:
        logger.warning(f"⚠️ Failed to pre-patch chromedriver at worker init: {e}")


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
    """Re-seed random module after fork to avoid all workers sharing the same sequence."""
    import random
    import os
    random.seed(os.urandom(32))
    logger.info("🎲 Random re-seeded in forked worker (pid=%d)", os.getpid())


@signals.worker_shutdown.connect
def _cleanup_chrome_on_worker_shutdown(**kwargs):
    """Kill all Chrome processes when Celery worker shuts down."""
    logger.info("🛑 Worker shutting down — cleaning up Chrome processes...")
    _reap_zombie_chrome()


@signals.task_postrun.connect
def _reap_zombies_after_task(**kwargs):
    """Reap zombie children after every task completes."""
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


@signals.task_failure.connect
def _cleanup_chrome_after_task_failure(**kwargs):
    """Clean up stale Chrome processes after a task fails.
    
    When uc.Chrome() fails with 'session not created', Chrome/chromedriver
    processes leak because browser_id is never returned. This targeted
    cleanup kills Chrome processes that have been running longer than the
    task time limit, which are certainly orphans.
    """
    import os, subprocess
    try:
        # Kill chromedriver processes that have no parent (orphaned)
        # Use ps to find chrome processes older than 5 minutes
        result = subprocess.run(
            ['sh', '-c',
             "ps -eo pid,etimes,comm | grep -E 'chrome|chromedriver' | awk '$2 > 300 {print $1}'"],
            capture_output=True, text=True, timeout=5
        )
        stale_pids = result.stdout.strip().split('\n')
        killed = 0
        for pid_str in stale_pids:
            pid_str = pid_str.strip()
            if pid_str and pid_str.isdigit():
                try:
                    os.kill(int(pid_str), 9)
                    killed += 1
                except (ProcessLookupError, PermissionError):
                    pass
        if killed:
            logger.info(f"🧹 Killed {killed} stale Chrome processes (>5min old) after task failure")
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
        is_logical_error = isinstance(retval, dict) and retval.get('status') in ('error', 'not_found')

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


# Configure logging for Celery
def setup_celery_logging():
    """Setup logging for Celery workers."""
    import logging.config

    LOGGING_CONFIG = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'default': {
                'format': '[%(asctime)s] %(levelname)s in %(module)s: %(message)s',
            },
            'detailed': {
                'format': '[%(asctime)s] %(levelname)s [%(name)s:%(lineno)d] %(message)s',
            }
        },
        'handlers': {
            'console': {
                'level': 'INFO',
                'class': 'logging.StreamHandler',
                'formatter': 'default',
            },
            'file': {
                'level': 'DEBUG',
                'class': 'logging.handlers.RotatingFileHandler',
                'filename': os.path.join(settings.logs_dir, 'celery.log'),
                'maxBytes': 10485760,  # 10MB
                'backupCount': 5,
                'formatter': 'detailed',
            },
        },
        'loggers': {
            '': {
                'handlers': ['console', 'file'],
                'level': 'INFO',
                'propagate': True,
            },
            'celery': {
                'handlers': ['console', 'file'],
                'level': 'INFO',
                'propagate': False,
            },
            'selenium': {
                'handlers': ['file'],
                'level': 'WARNING',
                'propagate': False,
            },
        }
    }

    logging.config.dictConfig(LOGGING_CONFIG)


# Worker ready signal
@signals.worker_ready.connect
def worker_ready(sender=None, **kwargs):
    """Called when worker is ready."""
    setup_celery_logging()
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