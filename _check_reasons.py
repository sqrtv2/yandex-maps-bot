from sqlalchemy import text
from app.database import SessionLocal

db = SessionLocal()

# Top failure reasons after 10:13
rows = db.execute(text(
    "SELECT "
    "  CASE "
    "    WHEN error_message LIKE '%captcha%' THEN 'captcha' "
    "    WHEN error_message LIKE '%Watchdog%' THEN 'watchdog_timeout' "
    "    WHEN error_message LIKE '%proxy%' OR error_message LIKE '%Proxy%' THEN 'proxy_error' "
    "    WHEN error_message LIKE '%renderer dead%' THEN 'renderer_dead' "
    "    WHEN error_message LIKE '%Manual cleanup%' THEN 'manual_cleanup' "
    "    WHEN error_message LIKE '%Wall-clock%' THEN 'wall_clock_budget' "
    "    WHEN error_message LIKE '%timeout%' OR error_message LIKE '%Timeout%' THEN 'timeout' "
    "    ELSE LEFT(error_message, 60) "
    "  END as reason, "
    "  count(*) as cnt "
    "FROM tasks "
    "WHERE task_type = 'yandex_search' AND status = 'failed' "
    "AND DATE(created_at) = '2026-03-29' "
    "AND created_at >= '2026-03-29 10:13:00' "
    "GROUP BY reason ORDER BY cnt DESC LIMIT 15"
)).fetchall()

print("=== FAILURE REASONS AFTER 10:13 ===")
for r in rows:
    print(f"  {r[1]:4d} | {r[0]}")

# Same for before 10:13
rows2 = db.execute(text(
    "SELECT "
    "  CASE "
    "    WHEN error_message LIKE '%captcha%' THEN 'captcha' "
    "    WHEN error_message LIKE '%Watchdog%' THEN 'watchdog_timeout' "
    "    WHEN error_message LIKE '%proxy%' OR error_message LIKE '%Proxy%' THEN 'proxy_error' "
    "    WHEN error_message LIKE '%renderer dead%' THEN 'renderer_dead' "
    "    WHEN error_message LIKE '%Manual cleanup%' THEN 'manual_cleanup' "
    "    WHEN error_message LIKE '%Wall-clock%' THEN 'wall_clock_budget' "
    "    WHEN error_message LIKE '%timeout%' OR error_message LIKE '%Timeout%' THEN 'timeout' "
    "    ELSE LEFT(error_message, 60) "
    "  END as reason, "
    "  count(*) as cnt "
    "FROM tasks "
    "WHERE task_type = 'yandex_search' AND status = 'failed' "
    "AND DATE(created_at) = '2026-03-29' "
    "AND created_at < '2026-03-29 10:13:00' "
    "GROUP BY reason ORDER BY cnt DESC LIMIT 10"
)).fetchall()

print()
print("=== FAILURE REASONS BEFORE 10:13 ===")
for r in rows2:
    print(f"  {r[1]:4d} | {r[0]}")

db.close()
