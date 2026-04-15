from app.database import SessionLocal
from sqlalchemy import text

db = SessionLocal()

stages = db.execute(text("""
    SELECT warmup_stage, status, COUNT(*) as cnt 
    FROM browser_profiles 
    GROUP BY warmup_stage, status 
    ORDER BY warmup_stage, status
""")).fetchall()

print("=== PROFILES BY STAGE & STATUS ===")
for s in stages:
    print(f"  Stage {s[0]}, status={s[1]}: {s[2]}")

total = db.execute(text("SELECT COUNT(*) FROM browser_profiles")).scalar()
active = db.execute(text("SELECT COUNT(*) FROM browser_profiles WHERE is_active=true")).scalar()
print(f"\nTotal profiles: {total}, Active: {active}")

warming = db.execute(text("SELECT COUNT(*) FROM browser_profiles WHERE status='warming_up'")).scalar()
print(f"Currently warming: {warming}")

recent = db.execute(text("""
    SELECT status, COUNT(*) FROM tasks 
    WHERE task_type='warmup' AND created_at > NOW() - INTERVAL '1 hour'
    GROUP BY status
""")).fetchall()
print(f"\n=== WARMUP TASKS (last 1 hour) ===")
for r in recent:
    print(f"  {r[0]}: {r[1]}")

daily = db.execute(text("""
    SELECT status, COUNT(*) FROM tasks 
    WHERE task_type='warmup' AND created_at > NOW() - INTERVAL '24 hours'
    GROUP BY status
""")).fetchall()
print(f"\n=== WARMUP TASKS (last 24 hours) ===")
for r in daily:
    print(f"  {r[0]}: {r[1]}")

avg_dur = db.execute(text("""
    SELECT AVG(EXTRACT(EPOCH FROM (completed_at - started_at))) as avg_seconds,
           MIN(EXTRACT(EPOCH FROM (completed_at - started_at))) as min_seconds,
           MAX(EXTRACT(EPOCH FROM (completed_at - started_at))) as max_seconds,
           COUNT(*) as cnt
    FROM tasks 
    WHERE task_type='warmup' AND status='completed' 
    AND created_at > NOW() - INTERVAL '24 hours'
    AND completed_at IS NOT NULL AND started_at IS NOT NULL
""")).fetchone()
if avg_dur and avg_dur[0]:
    print(f"\n=== WARMUP DURATION (completed, last 24h) ===")
    print(f"  Avg: {avg_dur[0]:.0f}s ({avg_dur[0]/60:.1f} min)")
    print(f"  Min: {avg_dur[1]:.0f}s ({avg_dur[1]/60:.1f} min)")
    print(f"  Max: {avg_dur[2]:.0f}s ({avg_dur[2]/60:.1f} min)")
    print(f"  Count: {avg_dur[3]}")

newly_warmed = db.execute(text("""
    SELECT COUNT(DISTINCT profile_id) FROM tasks 
    WHERE task_type='warmup' AND status='completed' 
    AND created_at > NOW() - INTERVAL '24 hours'
""")).scalar()
print(f"\nProfiles with completed warmup tasks (24h): {newly_warmed}")

hourly = db.execute(text("""
    SELECT date_trunc('hour', created_at) as hr, status, COUNT(*) 
    FROM tasks 
    WHERE task_type='warmup' AND created_at > NOW() - INTERVAL '6 hours'
    GROUP BY hr, status 
    ORDER BY hr DESC, status
""")).fetchall()
print(f"\n=== WARMUP HOURLY (last 6h) ===")
for h in hourly:
    print(f"  {h[0]}: {h[1]}={h[2]}")

db.close()
