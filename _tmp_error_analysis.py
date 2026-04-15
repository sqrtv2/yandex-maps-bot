import psycopg2
conn = psycopg2.connect("postgresql://postgres:password@postgres:5432/yandex_maps_bot")
cur = conn.cursor()

# Errors before/after fix (15:07)
cur.execute("""
SELECT 
    CASE WHEN updated_at < '2026-04-09 15:07' THEN 'before' ELSE 'after' END as period,
    SUBSTRING(error_message FROM 1 FOR 60) as msg,
    COUNT(*)
FROM tasks 
WHERE task_type='yandex_visit' AND status='failed' AND DATE(created_at)=CURRENT_DATE
GROUP BY period, msg
ORDER BY period, COUNT(*) DESC
""")
for row in cur.fetchall():
    print(f"  [{row[0]}] ({row[2]}) {row[1]}")

print()
cur.execute("""
SELECT 
    CASE WHEN updated_at < '2026-04-09 15:07' THEN 'before' ELSE 'after' END as period,
    status, COUNT(*)
FROM tasks 
WHERE task_type='yandex_visit' AND DATE(created_at)=CURRENT_DATE
GROUP BY period, status
ORDER BY period, COUNT(*) DESC
""")
print("Overall by period:")
for row in cur.fetchall():
    print(f"  [{row[0]}] {row[1]}: {row[2]}")

# Today_failed vs real DB
print()
cur.execute("""
SELECT id, title, today_visits, today_successful, today_failed
FROM yandex_map_targets WHERE is_active=true ORDER BY id
""")
print("Target counters vs real DB:")
for row in cur.fetchall():
    tid = row[0]
    cur2 = conn.cursor()
    cur2.execute("SELECT COUNT(*) FROM profile_target_visits WHERE target_id=%s AND DATE(visited_at)=CURRENT_DATE AND status='completed'", (tid,))
    real = cur2.fetchone()[0]
    print(f"  Target {tid} ({row[1]}): counter_success={row[3]}, counter_fail={row[4]}, real_completed={real}")

conn.close()
