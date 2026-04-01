from app.database import SessionLocal
from sqlalchemy import text

db = SessionLocal()

print("=== TASKS CREATED PER 5-MIN WINDOW (last 3h) ===")
rows = db.execute(text("""
    SELECT date_trunc('hour', created_at) + 
           (EXTRACT(minute FROM created_at)::int / 5) * interval '5 min' as time_window,
           COUNT(*) as created,
           SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) as ok,
           SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as fail
    FROM tasks 
    WHERE task_type = 'yandex_search' 
    AND created_at > NOW() - INTERVAL '3 hours'
    GROUP BY 1 ORDER BY 1 DESC LIMIT 30
""")).fetchall()
for r in rows:
    print(f"  {r[0]} | created={r[1]} ok={r[2]} fail={r[3]}")

print("\n=== PENDING DELAY FOR WATCHDOG TASKS ===")
rows = db.execute(text("""
    SELECT 
        CASE WHEN started_at IS NOT NULL 
             THEN EXTRACT(epoch FROM started_at - created_at)::int
             ELSE NULL END as delay_sec,
        error_message, created_at
    FROM tasks 
    WHERE task_type = 'yandex_search' AND status = 'failed'
    AND created_at > NOW() - INTERVAL '6 hours'
    AND error_message LIKE '%%Watchdog%%'
    ORDER BY created_at DESC LIMIT 20
""")).fetchall()
for r in rows:
    delay = f"{r[0]}s" if r[0] is not None else "NEVER_STARTED"
    msg = (r[1] or '')[:80]
    print(f"  {r[2]} | delay={delay} | {msg}")

print("\n=== CURRENT TASK STATES (1h) ===")
rows = db.execute(text("""
    SELECT status, COUNT(*) FROM tasks 
    WHERE task_type = 'yandex_search' AND created_at > NOW() - INTERVAL '1 hour'
    GROUP BY status
""")).fetchall()
for r in rows:
    print(f"  {r[0]}: {r[1]}")

print("\n=== AVG DURATION BY STATUS (6h) ===")
rows = db.execute(text("""
    SELECT status, 
           AVG(EXTRACT(epoch FROM completed_at - started_at))::int as avg_sec,
           MAX(EXTRACT(epoch FROM completed_at - started_at))::int as max_sec,
           COUNT(*) as cnt
    FROM tasks 
    WHERE task_type = 'yandex_search'
    AND started_at IS NOT NULL AND completed_at IS NOT NULL
    AND created_at > NOW() - INTERVAL '6 hours'
    GROUP BY status
""")).fetchall()
for r in rows:
    print(f"  {r[0]}: avg={r[1]}s max={r[2]}s cnt={r[3]}")

db.close()
