#!/usr/bin/env python3
from app.database import SessionLocal
from app.models import Task
from sqlalchemy import func
from datetime import datetime, timedelta

db = SessionLocal()
now = datetime.utcnow()

# Hourly breakdown last 6h
print("=== HOURLY BREAKDOWN (last 6h) ===")
for h in range(6, -1, -1):
    start = now - timedelta(hours=h+1)
    end = now - timedelta(hours=h)
    completed = db.query(func.count()).filter(
        Task.task_type == "yandex_search",
        Task.status == "completed",
        Task.updated_at.between(start, end)
    ).scalar()
    failed = db.query(func.count()).filter(
        Task.task_type == "yandex_search",
        Task.status == "failed",
        Task.updated_at.between(start, end)
    ).scalar()
    label = start.strftime("%H:%M") + "-" + end.strftime("%H:%M")
    print(f"  {label}: completed={completed} failed={failed}")

# Recent fail reasons
print("\n=== RECENT FAIL REASONS (last 3h) ===")
recent_fails = db.query(
    Task.error_message, func.count().label("cnt")
).filter(
    Task.task_type == "yandex_search",
    Task.status == "failed",
    Task.updated_at > now - timedelta(hours=3)
).group_by(Task.error_message).order_by(func.count().desc()).all()
for row in recent_fails[:10]:
    msg = str(row[0])[:120] if row[0] else "None"
    print(f"  [{row[1]}x] {msg}")

db.close()
