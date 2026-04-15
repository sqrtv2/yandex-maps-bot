from app.database import SessionLocal
from sqlalchemy import text
from datetime import datetime, timedelta

db = SessionLocal()

warmed = db.execute(text("SELECT COUNT(*) FROM browser_profiles WHERE is_active=true AND warmup_stage >= 2")).scalar()
print(f"Warmed profiles (is_active + warmup_stage>=2): {warmed}")

rows = db.execute(text("SELECT id, name, warmup_stage, is_mobile FROM browser_profiles WHERE is_active=true AND warmup_stage >= 2 ORDER BY id")).fetchall()
for r in rows:
    print(f"  ID={r[0]} stage={r[2]} mobile={r[3]}")

since = datetime.utcnow() - timedelta(minutes=60)
pids = db.execute(text("SELECT DISTINCT profile_id FROM tasks WHERE task_type='yandex_search' AND created_at > :s"), {"s": since}).fetchall()
print(f"\nProfiles used in last 60min: {[r[0] for r in pids]}")

in_prog = db.execute(text("SELECT profile_id, status FROM tasks WHERE task_type='yandex_search' AND status='in_progress'")).fetchall()
print(f"Currently in_progress profiles: {[(r[0],r[1]) for r in in_prog]}")

pending = db.execute(text("SELECT profile_id FROM tasks WHERE task_type='yandex_search' AND status='pending'")).fetchall()
print(f"Pending profiles: {[r[0] for r in pending]}")

# Check scheduler logic - what does it actually filter?
print("\n=== Last 60 min task counts by profile_id ===")
stats = db.execute(text("""
    SELECT profile_id, status, COUNT(*) 
    FROM tasks 
    WHERE task_type='yandex_search' AND created_at > :s
    GROUP BY profile_id, status
    ORDER BY profile_id, status
"""), {"s": since}).fetchall()
for r in stats:
    print(f"  profile={r[0]} status={r[1]} count={r[2]}")

db.close()
