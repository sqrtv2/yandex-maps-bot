import sys
sys.path.insert(0, '/app')
from app.database import SessionLocal
from sqlalchemy import text

db = SessionLocal()

# Get columns first
cols = db.execute(text(
    "SELECT column_name FROM information_schema.columns "
    "WHERE table_name='yandex_map_targets' ORDER BY ordinal_position"
)).fetchall()
print("=== yandex_map_targets columns ===")
for c in cols:
    print(f"  {c[0]}")

# All targets
targets = db.execute(text("SELECT * FROM yandex_map_targets WHERE is_active=true LIMIT 5")).fetchall()
print("\n=== Sample targets ===")
for t in targets:
    print(f"  {t}")

# Search targets columns
cols2 = db.execute(text(
    "SELECT column_name FROM information_schema.columns "
    "WHERE table_name='yandex_search_targets' ORDER BY ordinal_position"
)).fetchall()
print("\n=== yandex_search_targets columns ===")
for c in cols2:
    print(f"  {c[0]}")

# Recent tasks
tasks = db.execute(text(
    "SELECT task_type, status, COUNT(*) FROM tasks "
    "WHERE created_at > NOW() - INTERVAL '2 hours' "
    "GROUP BY task_type, status ORDER BY task_type, status"
)).fetchall()
print("\n=== Tasks (last 2h) ===")
for t in tasks:
    print(f"  {t[0]:30} {t[1]:15} count={t[2]}")

# Warmed profiles
warmed = db.execute(text(
    "SELECT status, COUNT(*) FROM browser_profiles "
    "WHERE warmup_completed=true AND is_active=true GROUP BY status"
)).fetchall()
print("\n=== Warmed profiles by status ===")
for w in warmed:
    print(f"  {w[0]}: {w[1]}")

db.close()
