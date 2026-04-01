#!/usr/bin/env python3
from app.database import SessionLocal
from app.models import Task
from sqlalchemy import func
from datetime import datetime, timedelta
import redis

db = SessionLocal()
now = datetime.utcnow()

print("=== SEARCH TASKS RIGHT NOW ===")
for status in ["pending", "in_progress", "retry"]:
    count = db.query(func.count()).filter(Task.task_type == "yandex_search", Task.status == status).scalar()
    print(f"  {status}: {count}")

print("\n=== IN_PROGRESS (зависшие) ===")
tasks = db.query(Task).filter(Task.task_type == "yandex_search", Task.status == "in_progress").all()
for t in tasks:
    age = int((now - t.started_at).total_seconds() / 60) if t.started_at else -1
    print(f"  id={t.id} age={age}min started={t.started_at}")

print("\n=== PENDING (не стартовали) ===")
tasks = db.query(Task).filter(Task.task_type == "yandex_search", Task.status == "pending").all()
for t in tasks[:15]:
    age = int((now - t.created_at).total_seconds() / 60) if t.created_at else -1
    print(f"  id={t.id} age={age}min created={t.created_at}")
if len(tasks) > 15:
    print(f"  ... and {len(tasks)-15} more")

print("\n=== RETRY ===")
tasks = db.query(Task).filter(Task.task_type == "yandex_search", Task.status == "retry").all()
for t in tasks:
    age = int((now - t.updated_at).total_seconds() / 60) if t.updated_at else -1
    print(f"  id={t.id} age={age}min updated={t.updated_at}")

r = redis.from_url("redis://redis:6379/0")
print(f"\n=== REDIS QUEUES ===")
print(f"  yandex_search: {r.llen('yandex_search')}")
print(f"  default: {r.llen('default')}")
print(f"  warmup: {r.llen('warmup')}")

db.close()
