#!/usr/bin/env python3
"""Check failure reasons for yandex_search tasks today."""
from app.database import SessionLocal
from app.models.task import Task
from collections import Counter
from datetime import datetime

db = SessionLocal()
today = datetime.utcnow().replace(hour=0, minute=0, second=0)
failed = db.query(Task).filter(
    Task.task_type == "yandex_search",
    Task.status == "failed",
    Task.created_at >= today
).all()

reasons = Counter()
for t in failed:
    err = str(t.error_message) if t.error_message else "unknown"
    if "not found" in err.lower() or "not_found" in err.lower():
        reasons["not_found (domain absent from SERP)"] += 1
    elif "captcha" in err.lower():
        reasons["captcha_failed"] += 1
    elif "timeout" in err.lower() or "timed out" in err.lower():
        reasons["timeout"] += 1
    elif "browser" in err.lower() or "chrome" in err.lower():
        reasons["browser_crash"] += 1
    elif "proxy" in err.lower():
        reasons["proxy_error"] += 1
    elif "soft time" in err.lower():
        reasons["soft_time_limit"] += 1
    else:
        reasons[err[:80]] += 1

print(f"Total failed today: {len(failed)}")
print(f"Total completed today: {db.query(Task).filter(Task.task_type == 'yandex_search', Task.status == 'completed', Task.created_at >= today).count()}")
print()
for reason, count in reasons.most_common(20):
    print(f"  {count:4d}  {reason}")
db.close()
