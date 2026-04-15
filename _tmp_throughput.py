import psycopg2
conn = psycopg2.connect("postgresql://postgres:password@postgres:5432/yandex_maps_bot")
cur = conn.cursor()

cur.execute("SELECT COUNT(*) FROM tasks WHERE task_type='yandex_visit' AND status IN ('pending','in_progress')")
print(f"Active tasks (pending+in_progress): {cur.fetchone()[0]}")

cur.execute("SELECT COUNT(*) FROM tasks WHERE task_type='yandex_visit' AND status='failed' AND error_message LIKE '%%не запустилась%%' AND updated_at > NOW() - INTERVAL '30 minutes'")
print(f"Auto-cancelled in last 30min: {cur.fetchone()[0]}")

cur.execute("SELECT COUNT(*) FROM tasks WHERE task_type='yandex_visit' AND created_at > NOW() - INTERVAL '10 minutes'")
print(f"Tasks created in last 10min: {cur.fetchone()[0]}")

cur.execute("SELECT COUNT(*) FROM tasks WHERE task_type='yandex_visit' AND status='completed' AND completed_at > NOW() - INTERVAL '10 minutes'")
print(f"Completed in last 10min: {cur.fetchone()[0]}")

# Check schedule_visits logic — how many tasks per call
cur.execute("SELECT COUNT(*) FROM tasks WHERE task_type='yandex_visit' AND status='failed' AND error_message LIKE '%%не запустилась%%' AND updated_at > NOW() - INTERVAL '5 minutes'")
print(f"Auto-cancelled in last 5min: {cur.fetchone()[0]}")

conn.close()
