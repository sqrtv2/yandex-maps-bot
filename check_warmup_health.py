#!/usr/bin/env python3
"""
Warmup health monitor — runs via cron every 10 minutes.
Checks if warmup is stuck (0 profiles warming, huge queue) and fixes it.

Install on server:
  crontab -e
  */10 * * * * docker exec yandex_maps_app python check_warmup_health.py >> /var/log/warmup_health.log 2>&1
"""
import sys
import json
import logging
from datetime import datetime, timedelta

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [WARMUP-HEALTH] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def check_and_fix():
    import redis as _redis
    from app.config import settings
    from app.database import SessionLocal
    from sqlalchemy import text

    r = _redis.Redis(host=settings.redis_host, port=settings.redis_port)
    db = SessionLocal()

    try:
        # 1. Check queue size
        warmup_queue_len = r.llen('warmup') or 0
        default_queue_len = r.llen('default') or 0

        # 2. Check how many profiles are currently warming
        warming_count = db.execute(
            text("SELECT COUNT(*) FROM browser_profiles WHERE status='warming_up'")
        ).scalar()

        # 3. Check profiles needing warmup
        needs_warmup = db.execute(
            text("SELECT COUNT(*) FROM browser_profiles WHERE warmup_completed=false AND is_active=true AND status='created'")
        ).scalar()

        # 4. Check warmed count
        warmed_count = db.execute(
            text("SELECT COUNT(*) FROM browser_profiles WHERE warmup_completed=true AND is_active=true")
        ).scalar()

        # 5. Check recent warmup task completions (last 30 min)
        recent_completed = db.execute(text("""
            SELECT COUNT(*) FROM tasks 
            WHERE task_type='warmup' AND status='completed' 
            AND completed_at > NOW() - INTERVAL '30 minutes'
        """)).scalar()

        # 6. Check recent warmup task failures (last 30 min)
        recent_failed = db.execute(text("""
            SELECT COUNT(*) FROM tasks 
            WHERE task_type='warmup' AND status='failed' 
            AND completed_at > NOW() - INTERVAL '30 minutes'
        """)).scalar()

        logger.info(
            f"Status: warming={warming_count}, needs_warmup={needs_warmup}, "
            f"warmed={warmed_count}, queue={warmup_queue_len}, default_queue={default_queue_len}, "
            f"recent_ok={recent_completed}, recent_fail={recent_failed}"
        )

        actions_taken = []

        # PROBLEM: Queue is large but nothing is warming
        if warmup_queue_len > 100 and warming_count == 0 and needs_warmup > 0:
            logger.warning(
                f"Queue clogged: {warmup_queue_len} tasks in queue but 0 warming. "
                f"Purging warmup queue..."
            )
            r.delete('warmup')
            actions_taken.append(f"purged_warmup_queue({warmup_queue_len})")

        # PROBLEM: Large queue even though no profiles need warmup (stale tasks)
        if warmup_queue_len > 200:
            logger.warning(f"Queue too large: {warmup_queue_len}. Purging...")
            r.delete('warmup')
            actions_taken.append(f"purged_warmup_queue({warmup_queue_len})")

        # PROBLEM: Default queue clogged (scheduler tasks stuck)
        if default_queue_len > 50:
            logger.warning(f"Default queue large: {default_queue_len}. Purging...")
            r.delete('default')
            actions_taken.append(f"purged_default_queue({default_queue_len})")

        # PROBLEM: No warmup activity for 30+ min and profiles need warmup
        if (warming_count == 0 and recent_completed == 0 and 
                needs_warmup > 0 and warmup_queue_len < 5):
            logger.warning(
                f"Warmup stalled: 0 warming, 0 completed recently, {needs_warmup} need warmup. "
                f"Triggering auto_schedule_initial_warmup..."
            )
            # Reset any stuck profiles
            db.execute(text("""
                UPDATE browser_profiles SET status='created', updated_at=NOW() 
                WHERE status='warming_up' AND updated_at < NOW() - INTERVAL '10 minutes'
            """))
            db.commit()

            # Trigger scheduler via Celery
            from tasks.warmup import auto_schedule_initial_warmup
            auto_schedule_initial_warmup.delay()
            actions_taken.append("triggered_auto_schedule")

        # PROBLEM: Stale in_progress warmup tasks (workers died with SIGKILL)
        stale_in_progress = db.execute(text("""
            SELECT COUNT(*) FROM tasks 
            WHERE task_type='warmup' AND status='in_progress' 
            AND started_at < NOW() - INTERVAL '80 minutes'
        """)).scalar()

        if stale_in_progress > 0:
            db.execute(text("""
                UPDATE tasks SET status='failed', 
                    error_message='Health monitor: task exceeded 80 min, likely SIGKILL',
                    completed_at=NOW()
                WHERE task_type='warmup' AND status='in_progress' 
                AND started_at < NOW() - INTERVAL '80 minutes'
            """))
            db.commit()
            actions_taken.append(f"failed_stale_tasks({stale_in_progress})")

        if actions_taken:
            logger.info(f"Actions: {', '.join(actions_taken)}")
        else:
            logger.info("OK — no action needed")

    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
    finally:
        db.close()


if __name__ == '__main__':
    check_and_fix()
