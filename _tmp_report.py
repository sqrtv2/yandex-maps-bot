import psycopg2, os
conn = psycopg2.connect(os.environ.get('YANDEX_BOT_DATABASE_URL', 'postgresql://postgres:password@postgres:5432/yandex_maps_bot'))
cur = conn.cursor()

sql = """
SELECT
    CASE
        WHEN status = 'completed' AND error_message IS NULL THEN 'completed'
        WHEN error_message LIKE '%%Captcha%%' THEN 'captcha'
        WHEN error_message LIKE '%%Watchdog%%' THEN 'watchdog'
        WHEN error_message LIKE '%%Wall-clock%%' THEN 'wall-clock'
        WHEN error_message LIKE '%%fallback%%' THEN 'fallback-fail'
        WHEN error_message LIKE '%%ERR_TUNNEL%%' THEN 'tunnel-fail'
        WHEN error_message LIKE '%%renderer dead%%' THEN 'renderer-dead'
        ELSE 'other'
    END as category,
    COUNT(*) as cnt
FROM tasks
WHERE task_type = 'yandex_search'
  AND updated_at > NOW() - INTERVAL %s
GROUP BY 1
ORDER BY cnt DESC
"""

for period in ['2 hours', '24 hours']:
    cur.execute(sql, (period,))
    rows = cur.fetchall()
    total = sum(r[1] for r in rows)
    print(f"=== Last {period} (total: {total}) ===")
    for r in rows:
        pct = r[1] * 100 // total if total else 0
        print(f"  {r[1]:3d} ({pct:2d}%)  {r[0]}")
    print()

conn.close()
