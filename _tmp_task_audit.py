#!/usr/bin/env python3
from app.database import SessionLocal
from app.models import Task
from datetime import datetime
from collections import Counter

db = SessionLocal()
today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

# Count all search tasks by status - updated today
tasks = db.query(Task).filter(
    Task.task_type == 'yandex_search',
    Task.updated_at >= today,
).all()

status_counts = Counter(t.status for t in tasks)
print("=== Search tasks UPDATED today ===")
for s, c in status_counts.most_common():
    print(f"  {s:15s}: {c}")
print(f"  TOTAL: {len(tasks)}")

# Count by created_at
tasks2 = db.query(Task).filter(
    Task.task_type == 'yandex_search',
    Task.created_at >= today,
).all()
status_counts2 = Counter(t.status for t in tasks2)
print("\n=== Search tasks CREATED today ===")
for s, c in status_counts2.most_common():
    print(f"  {s:15s}: {c}")
print(f"  TOTAL: {len(tasks2)}")

# Total in DB
total_all = db.query(Task).filter(Task.task_type == 'yandex_search').count()
print(f"\nTotal search tasks in DB: {total_all}")

# Check if tasks get retried (same profile+keyword appearing multiple times)
# This would explain the discrepancy
failed_tasks = [t for t in tasks if t.status in ('failed', 'error')]
print(f"\nFailed/error tasks updated today: {len(failed_tasks)}")

# Check retry count distribution
retry_counts = Counter()
for t in failed_tasks:
    p = t.parameters or {}
    retry = p.get('retry_count', 0)
    retry_counts[retry] += 1
print("Retry count distribution of failed tasks:")
for r, c in sorted(retry_counts.items()):
    print(f"  retry={r}: {c}")

# Check if tasks have celery retries
# Check unique task IDs vs total
task_ids = [t.id for t in tasks]
print(f"\nUnique task IDs updated today: {len(set(task_ids))}")
print(f"Total task records updated today: {len(tasks)}")

# Created today by target
print("\n=== CREATED today by target_id ===")
target_counts_created = Counter()
for t in tasks2:
    tid = (t.parameters or {}).get('target_id', '?')
    target_counts_created[tid] += 1
for tid, cnt in target_counts_created.most_common():
    print(f"  target_id={tid}: {cnt}")

# Failed today by target  
print("\n=== FAILED/ERROR today by target_id (updated_at) ===")
target_counts_failed = Counter()
for t in failed_tasks:
    tid = (t.parameters or {}).get('target_id', '?')
    target_counts_failed[tid] += 1
for tid, cnt in target_counts_failed.most_common():
    print(f"  target_id={tid}: {cnt}")

db.close()
