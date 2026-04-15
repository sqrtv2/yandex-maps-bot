from app.database import SessionLocal
from sqlalchemy import text
from datetime import datetime
import redis

db = SessionLocal()

counts = db.execute(text("SELECT status, COUNT(*) FROM tasks WHERE task_type='yandex_search' AND status IN ('in_progress', 'pending') GROUP BY status")).fetchall()
for r in counts:
    print(f"  {r[0]}: {r[1]}")

result = db.execute(text("UPDATE tasks SET status='failed', error_message=COALESCE(error_message,'') || ' [cleanup]', completed_at=:now WHERE task_type='yandex_search' AND status IN ('in_progress', 'pending')"), {"now": datetime.utcnow()})
print(f"Marked {result.rowcount} tasks as failed")
db.commit()

r = redis.Redis(host='redis', port=6379)
qlen = r.llen('yandex_search')
r.delete('yandex_search')
print(f"Purged Redis queue ({qlen} messages)")

r.delete('scheduler:schedule_search_visits:lock')
print("Scheduler lock removed - will run on next beat")
db.close()
