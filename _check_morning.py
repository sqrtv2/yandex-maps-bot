from sqlalchemy import text
from app.database import SessionLocal

db = SessionLocal()

rows = db.execute(text(
    "SELECT count(*), "
    "count(*) filter (where status = 'completed'), "
    "count(*) filter (where status = 'failed'), "
    "count(*) filter (where error_message like '%captcha%') "
    "FROM tasks WHERE task_type = 'yandex_search' "
    "AND DATE(created_at) = '2026-03-29' "
    "AND created_at < '2026-03-29 10:13:00'"
)).fetchone()
print(f"Before 10:13 - total: {rows[0]}, completed: {rows[1]}, failed: {rows[2]}, captcha_fails: {rows[3]}")

rows2 = db.execute(text(
    "SELECT count(*), "
    "count(*) filter (where status = 'completed'), "
    "count(*) filter (where status = 'failed'), "
    "count(*) filter (where error_message like '%captcha%') "
    "FROM tasks WHERE task_type = 'yandex_search' "
    "AND DATE(created_at) = '2026-03-29' "
    "AND created_at >= '2026-03-29 10:13:00'"
)).fetchone()
print(f"After 10:13  - total: {rows2[0]}, completed: {rows2[1]}, failed: {rows2[2]}, captcha_fails: {rows2[3]}")

rows3 = db.execute(text(
    "SELECT completed_at, name FROM tasks "
    "WHERE task_type = 'yandex_search' AND status = 'completed' "
    "ORDER BY completed_at DESC LIMIT 5"
)).fetchall()
print()
print("Last 5 completed:")
for r in rows3:
    print(f"  {r[0]} | {r[1][:60]}")

db.close()
