from app.database import SessionLocal
from sqlalchemy import text
from datetime import datetime, timedelta
db = SessionLocal()

rows = db.execute(text("SELECT id, status, error_message, created_at, completed_at FROM tasks WHERE task_type='yandex_search' AND error_message='Container restarted' ORDER BY id DESC LIMIT 5")).fetchall()
print("=== Tasks with 'Container restarted' ===")
for r in rows:
    print(f"  ID={r[0]} status={r[1]} created={r[3]} completed={r[4]}")

print()
rows2 = db.execute(text("SELECT id, status, error_message, created_at, started_at FROM tasks WHERE task_type='yandex_search' AND status IN ('in_progress', 'pending') ORDER BY id DESC LIMIT 10")).fetchall()
print(f"=== Currently active: {len(rows2)} tasks ===")
for r in rows2:
    print(f"  ID={r[0]} status={r[1]} err={r[2]} created={r[3]} started={r[4]}")

print()
since = datetime.utcnow() - timedelta(minutes=30)
rows3 = db.execute(text("SELECT status, COUNT(*) FROM tasks WHERE task_type='yandex_search' AND created_at > :s GROUP BY status ORDER BY COUNT(*) DESC"), {"s": since}).fetchall()
print("=== Last 30 min ===")
for r in rows3:
    print(f"  {r[0]}: {r[1]}")
db.close()
