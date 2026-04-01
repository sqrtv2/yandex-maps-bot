from app.database import SessionLocal
from sqlalchemy import text

db = SessionLocal()

# Recent search tasks stats
stats = db.execute(text("""
    SELECT status, COUNT(*) as cnt 
    FROM tasks 
    WHERE task_type = 'yandex_search' 
    AND created_at > NOW() - INTERVAL '1 day'
    GROUP BY status
    ORDER BY cnt DESC
""")).fetchall()
print("=== SEARCH TASKS LAST 24H ===")
for s in stats:
    print(f"  {s[0]}: {s[1]}")

# Error reasons
errors = db.execute(text("""
    SELECT error_message, COUNT(*) as cnt 
    FROM tasks 
    WHERE task_type = 'yandex_search' 
    AND status = 'failed'
    AND created_at > NOW() - INTERVAL '1 day'
    GROUP BY error_message
    ORDER BY cnt DESC
    LIMIT 20
""")).fetchall()
print("\n=== TOP ERROR REASONS ===")
for e in errors:
    msg = (e[0] or 'None')[:150]
    print(f"  [{e[1]}] {msg}")

# Success rate over last 7 days by day
daily = db.execute(text("""
    SELECT created_at::date as day,
           SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) as ok,
           SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as fail,
           COUNT(*) as total
    FROM tasks 
    WHERE task_type = 'yandex_search' 
    AND created_at > NOW() - INTERVAL '7 days'
    GROUP BY created_at::date
    ORDER BY day DESC
""")).fetchall()
print("\n=== DAILY STATS (7 days) ===")
for d in daily:
    rate = round(d[1]*100/d[3], 1) if d[3] > 0 else 0
    print(f"  {d[0]}: ok={d[1]} fail={d[2]} total={d[3]} rate={rate}%")

# Recent failed tasks - last 10
recent = db.execute(text("""
    SELECT id, created_at, error_message
    FROM tasks 
    WHERE task_type = 'yandex_search' 
    AND status = 'failed'
    AND created_at > NOW() - INTERVAL '1 day'
    ORDER BY created_at DESC
    LIMIT 10
""")).fetchall()
print("\n=== LAST 10 FAILURES ===")
for r in recent:
    msg = (r[2] or 'None')[:150]
    print(f"  [{r[0]}] {r[1]} - {msg}")

db.close()
