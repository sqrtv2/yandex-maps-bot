from app.database import SessionLocal
from sqlalchemy import text

db = SessionLocal()

rows = db.execute(text("""
    SELECT profile_id, COUNT(*) as cnt 
    FROM tasks 
    WHERE task_type = 'yandex_search' 
    AND status IN ('in_progress', 'pending')
    AND profile_id IS NOT NULL
    GROUP BY profile_id 
    HAVING COUNT(*) > 1
""")).fetchall()

if rows:
    print("COLLISION DETECTED:")
    for r in rows:
        print(f"  Profile {r[0]}: {r[1]} concurrent tasks")
else:
    print("No collisions - each profile has at most 1 active task")

total = db.execute(text("SELECT COUNT(*) FROM tasks WHERE task_type='yandex_search' AND status IN ('in_progress', 'pending')")).scalar()
print(f"Total active tasks: {total}")

active = db.execute(text("SELECT profile_id, status FROM tasks WHERE task_type='yandex_search' AND status IN ('in_progress', 'pending') ORDER BY profile_id")).fetchall()
for r in active:
    print(f"  Profile {r[0]}: {r[1]}")
db.close()
