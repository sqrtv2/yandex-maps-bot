from app.database import SessionLocal
from app.models.task import Task
from app.models.browser_profile import BrowserProfile
from sqlalchemy import func, desc
from datetime import datetime, timedelta

db = SessionLocal()

now = datetime.utcnow()
h24 = now - timedelta(hours=24)

total_24h = db.query(func.count(Task.id)).filter(Task.task_type == "warmup", Task.created_at >= h24).scalar()
completed_24h = db.query(func.count(Task.id)).filter(Task.task_type == "warmup", Task.status == "completed", Task.created_at >= h24).scalar()
failed_24h = db.query(func.count(Task.id)).filter(Task.task_type == "warmup", Task.status == "failed", Task.created_at >= h24).scalar()
running_24h = db.query(func.count(Task.id)).filter(Task.task_type == "warmup", Task.status == "in_progress", Task.created_at >= h24).scalar()
pending_24h = db.query(func.count(Task.id)).filter(Task.task_type == "warmup", Task.status == "pending", Task.created_at >= h24).scalar()

print("=== Warmup Tasks (last 24h) ===")
print(f"Total: {total_24h}")
print(f"Completed: {completed_24h}")
print(f"Failed: {failed_24h}")
print(f"In Progress: {running_24h}")
print(f"Pending: {pending_24h}")
print()

recent = db.query(Task).filter(Task.task_type == "warmup").order_by(desc(Task.created_at)).limit(10).all()
print("=== Last 10 warmup tasks ===")
for t in recent:
    profile_id = t.profile_id or "?"
    dur = ""
    if t.started_at and t.completed_at:
        dur = f" ({(t.completed_at - t.started_at).total_seconds():.0f}s)"
    err = ""
    if t.error_message:
        err = f" ERR: {t.error_message[:80]}"
    print(f"  [{t.status}] profile={profile_id} created={t.created_at}{dur}{err}")
print()

recent_warmed = db.query(BrowserProfile).filter(BrowserProfile.first_warmup_at != None).order_by(desc(BrowserProfile.first_warmup_at)).limit(10).all()
print("=== Last 10 profiles with warmup activity ===")
for p in recent_warmed:
    print(f"  Profile {p.id}: stage={p.warmup_stage} status={p.status} first_warmup={p.first_warmup_at} completed={p.warmup_completed}")
print()

# Check total warmup tasks ever
total_ever = db.query(func.count(Task.id)).filter(Task.task_type == "warmup").scalar()
print(f"Total warmup tasks EVER: {total_ever}")

# Check all task types
task_types = db.query(Task.task_type, func.count(Task.id)).group_by(Task.task_type).all()
print("All task types:")
for tt, c in task_types:
    print(f"  {tt}: {c}")
print()

# Check beat schedule - look at latest scheduled items
from app.models.task import Task
latest_tasks = db.query(Task).order_by(desc(Task.created_at)).limit(5).all()
print("=== Latest 5 tasks (any type) ===")
for t in latest_tasks:
    print(f"  [{t.status}] type={t.task_type} profile={t.profile_id} created={t.created_at}")
print()

# Check celery inspect
import subprocess
result = subprocess.run(["celery", "-A", "tasks.celery_app", "inspect", "active", "--timeout=5"], capture_output=True, text=True)
print("=== Celery active tasks ===")
print(result.stdout[:2000] if result.stdout else "No output")
print(result.stderr[:500] if result.stderr else "")

db.close()
