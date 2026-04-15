from app.database import SessionLocal
from sqlalchemy import text
import redis

db = SessionLocal()

print("=== Task status ===")
rows = db.execute(text("""
    SELECT status, COUNT(*) FROM tasks 
    WHERE task_type='yandex_search' AND status IN ('in_progress','pending')
    GROUP BY status ORDER BY status
""")).fetchall()
for r in rows:
    print(f"  {r[0]}: {r[1]}")

total = sum(r[1] for r in rows)
print(f"  TOTAL active: {total}")

print("\n=== Profile collision check ===")
dupes = db.execute(text("""
    SELECT profile_id, COUNT(*) as cnt FROM tasks 
    WHERE task_type='yandex_search' AND status IN ('in_progress','pending') AND profile_id IS NOT NULL
    GROUP BY profile_id HAVING COUNT(*) > 1
""")).fetchall()
if dupes:
    for d in dupes:
        print(f"  COLLISION: profile {d[0]} has {d[1]} tasks!")
else:
    print("  No collisions")

print("\n=== Redis queue ===")
r = redis.Redis(host='redis', port=6379)
print(f"  yandex_search queue: {r.llen('yandex_search')}")

print("\n=== Worker concurrency ===")
print("  docker-compose concurrency=10")

db.close()
