#!/usr/bin/env python3
"""Check why search tasks are stuck."""
from app.database import SessionLocal
from app.models import Task
from sqlalchemy import func
from datetime import datetime, timedelta

db = SessionLocal()

# Count tasks by status
print("=== SEARCH TASK STATUS COUNTS ===")
results = db.query(Task.status, func.count()).filter(Task.task_type == "yandex_search").group_by(Task.status).all()
for status, count in results:
    print(f"  {status}: {count}")

# Check in_progress tasks
in_progress = db.query(Task).filter(Task.task_type == "yandex_search", Task.status == "in_progress").all()
print(f"\n=== IN_PROGRESS TASKS ({len(in_progress)}) ===")
for t in in_progress[:20]:
    age = (datetime.utcnow() - t.started_at).total_seconds() / 60 if t.started_at else -1
    print(f"  id={t.id} started={t.started_at} age={age:.1f}min")

# Check pending tasks
pending = db.query(Task).filter(Task.task_type == "yandex_search", Task.status == "pending").all()
print(f"\n=== PENDING TASKS ({len(pending)}) ===")
for t in pending[:10]:
    age = (datetime.utcnow() - t.created_at).total_seconds() / 60 if t.created_at else -1
    print(f"  id={t.id} created={t.created_at} age={age:.1f}min")

# Check recent completed/failed
recent = db.query(Task).filter(
    Task.task_type == "yandex_search",
    Task.status.in_(["completed", "failed"]),
    Task.updated_at > datetime.utcnow() - timedelta(hours=2)
).order_by(Task.updated_at.desc()).limit(10).all()
print(f"\n=== RECENT COMPLETED/FAILED (last 2h) ===")
for t in recent:
    print(f"  id={t.id} status={t.status} updated={t.updated_at} error={str(t.error_message)[:100] if t.error_message else ''}")

# Check Redis queue
try:
    import redis
    r = redis.from_url("redis://redis:6379/0")
    qs = r.llen("yandex_search")
    print(f"\n=== REDIS QUEUE ===")
    print(f"  yandex_search queue length: {qs}")
except Exception as e:
    print(f"\n  Redis error: {e}")

# Check active search targets
from app.models import SearchTarget
targets = db.query(SearchTarget).filter(SearchTarget.is_active == True).all()
print(f"\n=== ACTIVE SEARCH TARGETS ({len(targets)}) ===")
for t in targets:
    daily_done = db.query(func.count()).filter(
        Task.task_type == "yandex_search",
        Task.search_target_id == t.id,
        Task.created_at > datetime.utcnow() - timedelta(hours=24),
        Task.status == "completed"
    ).scalar()
    print(f"  id={t.id} domain={t.domain} daily_limit={t.daily_click_limit} done_today={daily_done}")

# Check celery workers
try:
    from tasks.celery_app import celery_app
    insp = celery_app.control.inspect(timeout=5)
    active = insp.active()
    if active:
        for worker, tasks in active.items():
            print(f"\n=== WORKER: {worker} ({len(tasks)} active) ===")
            for t in tasks[:5]:
                print(f"  {t['name']} id={t['id'][:8]}... runtime={t.get('time_start', 'N/A')}")
    else:
        print("\n=== NO ACTIVE WORKERS RESPONDING ===")
except Exception as e:
    print(f"\nCelery inspect error: {e}")

db.close()
