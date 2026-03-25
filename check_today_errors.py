import sqlalchemy as sa
from datetime import datetime

engine = sa.create_engine("postgresql://postgres:password@postgres:5432/yandex_maps_bot")
conn = engine.connect()
today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

r = conn.execute(sa.text("SELECT error_category, COUNT(*) as cnt FROM error_logs WHERE created_at >= :today GROUP BY error_category ORDER BY cnt DESC"), {"today": today})
print("=== ERROR CATEGORIES TODAY ===")
total = 0
for row in r:
    print(f"  {row[0]}: {row[1]}")
    total += row[1]
print(f"  TOTAL: {total}")

print()
r2 = conn.execute(sa.text("SELECT status, COUNT(*) as cnt FROM tasks WHERE task_type='yandex_search' AND created_at >= :today GROUP BY status ORDER BY cnt DESC"), {"today": today})
print("=== SEARCH TASKS TODAY ===")
for row in r2:
    print(f"  {row[0]}: {row[1]}")

print()
r3 = conn.execute(sa.text("SELECT result_status, COUNT(*) as cnt FROM profile_search_visits WHERE visited_at >= :today GROUP BY result_status ORDER BY cnt DESC"), {"today": today})
print("=== SEARCH VISIT RESULTS TODAY ===")
for row in r3:
    print(f"  {row[0]}: {row[1]}")

print()
r4 = conn.execute(sa.text("SELECT error_message, COUNT(*) as cnt FROM error_logs WHERE created_at >= :today GROUP BY error_message ORDER BY cnt DESC LIMIT 15"), {"today": today})
print("=== TOP ERROR MESSAGES TODAY ===")
for row in r4:
    msg = str(row[0])[:120] if row[0] else "None"
    print(f"  [{row[1]}x] {msg}")

print()
r5 = conn.execute(sa.text("SELECT fail_reason, COUNT(*) as cnt FROM profile_search_visits WHERE visited_at >= :today AND result_status != 'clicked' GROUP BY fail_reason ORDER BY cnt DESC LIMIT 10"), {"today": today})
print("=== SEARCH FAIL REASONS TODAY ===")
for row in r5:
    print(f"  {row[0]}: {row[1]}")

conn.close()
