import sqlalchemy as sa
from datetime import datetime, timedelta

engine = sa.create_engine("postgresql://postgres:password@postgres:5432/yandex_maps_bot")
conn = engine.connect()

# Get proxy table columns
r = conn.execute(sa.text("SELECT column_name FROM information_schema.columns WHERE table_name='proxy_servers' ORDER BY ordinal_position"))
cols = [row[0] for row in r]
print("Proxy columns:", cols)

# Check proxies
r = conn.execute(sa.text("SELECT * FROM proxy_servers LIMIT 5"))
print("\n=== PROXIES (first 5) ===")
for row in r:
    print(f"  {row}")

# Hourly error distribution
print("\n=== ERRORS BY HOUR (last 24h) ===")
r2 = conn.execute(sa.text("SELECT DATE_TRUNC('hour', created_at) as hr, error_category, COUNT(*) FROM error_logs WHERE created_at >= NOW() - INTERVAL '24 hours' GROUP BY hr, error_category ORDER BY hr DESC, COUNT(*) DESC LIMIT 40"))
for row in r2:
    print(f"  {row[0]}: {row[1]} = {row[2]}")

# Task results by hour
print("\n=== SEARCH TASKS BY HOUR (last 24h) ===")
r3 = conn.execute(sa.text("SELECT DATE_TRUNC('hour', created_at) as hr, status, COUNT(*) FROM tasks WHERE task_type='yandex_search' AND created_at >= NOW() - INTERVAL '24 hours' GROUP BY hr, status ORDER BY hr DESC, COUNT(*) DESC LIMIT 40"))
for row in r3:
    print(f"  {row[0]}: {row[1]} = {row[2]}")

# Check error messages for worker_killed pattern
print("\n=== WORKER_KILLED / SIGKILL ERRORS ===")
r4 = conn.execute(sa.text("SELECT COUNT(*) FROM error_logs WHERE created_at >= NOW() - INTERVAL '24 hours' AND (error_message LIKE '%worker_killed%' OR error_message LIKE '%SIGKILL%' OR error_message LIKE '%signal 9%' OR error_category='worker_killed')"))
for row in r4:
    print(f"  worker_killed errors: {row[0]}")

# Recent failed task error messages
print("\n=== RECENT FAILED TASK RESULTS ===")
r5 = conn.execute(sa.text("SELECT result, error_message FROM tasks WHERE task_type='yandex_search' AND status='failed' AND created_at >= NOW() - INTERVAL '6 hours' ORDER BY created_at DESC LIMIT 10"))
for row in r5:
    res = str(row[0])[:100] if row[0] else "None"
    err = str(row[1])[:120] if row[1] else "None"
    print(f"  result={res}")
    print(f"  error={err}")
    print()

conn.close()
