from app.database import SessionLocal
from app.models.task import Task
from datetime import datetime, timedelta
from sqlalchemy import func

db = SessionLocal()

cutoff = datetime.utcnow() - timedelta(hours=3)
recent = db.query(Task).filter(
    Task.task_type == "yandex_search",
    Task.completed_at > cutoff
).order_by(Task.completed_at.desc()).limit(20).all()

print("Tasks completed in last 3h:")
for t in recent:
    duration = (t.completed_at - t.started_at).total_seconds() if t.started_at else 0
    err = str(t.error_message)[:70] if t.error_message else "SUCCESS"
    s = str(t.started_at)[11:19] if t.started_at else "N/A"
    e = str(t.completed_at)[11:19]
    print("  #%d %-9s start=%s end=%s dur=%ds | %s" % (t.id, t.status, s, e, duration, err))

today = datetime.utcnow().replace(hour=0, minute=0, second=0)
comp = db.query(func.count(Task.id)).filter(
    Task.task_type == "yandex_search",
    Task.status == "completed",
    Task.completed_at > today
).scalar()
fail = db.query(func.count(Task.id)).filter(
    Task.task_type == "yandex_search",
    Task.status == "failed",
    Task.completed_at > today
).scalar()
total = comp + fail
rate = comp * 100.0 / total if total else 0
print("\nToday: completed=%d, failed=%d, rate=%.1f%%" % (comp, fail, rate))

# Check scheduler runs
import redis
r = redis.Redis(host='redis', port=6379, db=0)
lock_key = 'search_scheduler_lock'
lock_val = r.get(lock_key)
print("\nScheduler lock: %s" % lock_val)
print("Redis yandex_search queue: %d" % r.llen('yandex_search'))

db.close()
