#!/usr/bin/env python3
"""Clear all stuck search tasks and purge Redis queue."""
from app.database import SessionLocal
from app.models import Task
from sqlalchemy import func
from datetime import datetime
import redis

db = SessionLocal()
now = datetime.utcnow()

# Count before
print("=== BEFORE CLEANUP ===")
for status in ["pending", "in_progress", "retry"]:
    count = db.query(func.count()).filter(Task.task_type == "yandex_search", Task.status == status).scalar()
    print(f"  {status}: {count}")

# Mark all pending/in_progress/retry as failed
updated = 0
for status in ["pending", "in_progress", "retry"]:
    count = db.query(Task).filter(
        Task.task_type == "yandex_search",
        Task.status == status
    ).update({"status": "failed", "error_message": f"Manual cleanup: was {status}", "updated_at": now})
    updated += count
    print(f"  Marked {count} {status} -> failed")

db.commit()
print(f"\nTotal cleaned: {updated}")

# Purge Redis queue
r = redis.from_url("redis://redis:6379/0")
deleted = r.delete("yandex_search")
print(f"Redis yandex_search queue purged: {deleted}")

# Verify
print("\n=== AFTER CLEANUP ===")
for status in ["pending", "in_progress", "retry"]:
    count = db.query(func.count()).filter(Task.task_type == "yandex_search", Task.status == status).scalar()
    print(f"  {status}: {count}")
print(f"  Redis yandex_search: {r.llen('yandex_search')}")

db.close()
print("\nDone! Queue is clean.")
