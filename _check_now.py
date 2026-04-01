from sqlalchemy import text
from app.database import SessionLocal
db = SessionLocal()

stats = db.execute(text("SELECT status, count(*) FROM tasks WHERE task_type = 'yandex_search' AND DATE(created_at) = '2026-03-29' GROUP BY status ORDER BY count(*) DESC")).fetchall()
print("=== TODAY STATS ===")
for s in stats:
    print(f"  {s[0]}: {s[1]}")

fails = db.execute(text("SELECT error_message, updated_at FROM tasks WHERE task_type = 'yandex_search' AND status = 'failed' AND updated_at >= '2026-03-29 16:50:00' ORDER BY updated_at DESC LIMIT 20")).fetchall()
print()
print("=== RECENT FAILURES (since 16:50) ===")
for f in fails:
    err = (f[0] or "-")[:120]
    print(f"  {f[1]} | {err}")

comp = db.execute(text("SELECT completed_at, name FROM tasks WHERE task_type = 'yandex_search' AND status = 'completed' AND DATE(created_at) = '2026-03-29' ORDER BY completed_at DESC LIMIT 5")).fetchall()
print()
print("=== RECENT COMPLETED ===")
for c in comp:
    print(f"  {c[0]} | {c[1][:60]}")

# Current queue
cur = db.execute(text("SELECT status, count(*) FROM tasks WHERE task_type = 'yandex_search' AND status IN ('pending','in_progress','retry') GROUP BY status")).fetchall()
print()
print("=== CURRENT QUEUE ===")
for c in cur:
    print(f"  {c[0]}: {c[1]}")

db.close()
