#!/usr/bin/env python3
from app.database import SessionLocal
from app.models.browser_profile import BrowserProfile
from app.models import Task
from datetime import datetime, timedelta

db = SessionLocal()
now = datetime.utcnow()

# Warmed profiles sorted by update time
warmed = db.query(BrowserProfile).filter(
    BrowserProfile.warmup_completed == True,
    BrowserProfile.status == 'warmed',
).order_by(BrowserProfile.updated_at.desc()).all()

print(f"Total warmed: {len(warmed)}")
print("\nLast 10 warmed profiles:")
for p in warmed[:10]:
    age = (now - p.updated_at).total_seconds() / 3600 if p.updated_at else 0
    print(f"  id={p.id} | sessions={p.warmup_sessions_count} | "
          f"time_spent={p.warmup_time_spent}s | "
          f"first={p.first_warmup_at} | updated={p.updated_at} ({age:.1f}h ago)")

# Warmup throughput
h24 = now - timedelta(hours=24)
h12 = now - timedelta(hours=12)
h6 = now - timedelta(hours=6)
h1 = now - timedelta(hours=1)
w24 = len([p for p in warmed if p.updated_at and p.updated_at >= h24])
w12 = len([p for p in warmed if p.updated_at and p.updated_at >= h12])
w6 = len([p for p in warmed if p.updated_at and p.updated_at >= h6])
w1 = len([p for p in warmed if p.updated_at and p.updated_at >= h1])
print(f"\nWarmed in last 24h: {w24}")
print(f"Warmed in last 12h: {w12}")
print(f"Warmed in last 6h: {w6}")
print(f"Warmed in last 1h: {w1}")

# Currently warming
warming = db.query(BrowserProfile).filter(BrowserProfile.status == 'warming').all()
print(f"\nCurrently warming: {len(warming)}")
for p in warming[:5]:
    age = (now - p.updated_at).total_seconds() / 60 if p.updated_at else 0
    print(f"  id={p.id} | sessions={p.warmup_sessions_count}/{p.warmup_stage} | updated={p.updated_at} ({age:.0f}m ago)")

# Warmup tasks
warmup_pending = db.query(Task).filter(
    Task.task_type.like('%warmup%'),
    Task.status.in_(['pending', 'in_progress']),
).count()
print(f"\nWarmup tasks pending/in_progress: {warmup_pending}")

# Check warmup workers active
warmup_active = db.query(Task).filter(
    Task.task_type.like('%warmup%'),
    Task.status == 'in_progress',
).all()
print(f"Warmup tasks in_progress: {len(warmup_active)}")

# Avg warmup time
if warmed:
    times = [p.warmup_time_spent for p in warmed if p.warmup_time_spent and p.warmup_time_spent > 0]
    if times:
        avg_t = sum(times) / len(times)
        print(f"\nAvg warmup time per profile: {avg_t:.0f}s ({avg_t/60:.1f} min)")
        print(f"Min: {min(times)}s, Max: {max(times)}s")
    sessions = [p.warmup_sessions_count for p in warmed if p.warmup_sessions_count]
    if sessions:
        print(f"Avg sessions to warm: {sum(sessions)/len(sessions):.1f}")

# Retired profiles
retired = db.query(BrowserProfile).filter(BrowserProfile.status == 'retired').count()
created = db.query(BrowserProfile).filter(BrowserProfile.status == 'created').count()
print(f"\nRetired: {retired}, Created (not started): {created}")

db.close()
