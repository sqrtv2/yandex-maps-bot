#!/usr/bin/env python3
import psycopg2
conn = psycopg2.connect(host="postgres", dbname="yandex_maps_bot", user="postgres", password="password")
cur = conn.cursor()

cur.execute("SELECT status, COUNT(*) FROM browser_profiles GROUP BY status ORDER BY COUNT(*) DESC")
print("Status breakdown:")
for r in cur.fetchall():
    print(f"  {r[0]}: {r[1]}")

cur.execute("SELECT warmup_stage, COUNT(*) FROM browser_profiles GROUP BY warmup_stage ORDER BY warmup_stage")
print("\nWarmup stages:")
for r in cur.fetchall():
    print(f"  stage {r[0]}: {r[1]}")

cur.execute("SELECT warmup_sessions_count, COUNT(*) FROM browser_profiles GROUP BY warmup_sessions_count ORDER BY warmup_sessions_count")
print("\nWarmup sessions count:")
for r in cur.fetchall():
    print(f"  sessions={r[0]}: {r[1]}")

cur.execute("SELECT warmup_completed, COUNT(*) FROM browser_profiles GROUP BY warmup_completed")
print("\nWarmup completed:")
for r in cur.fetchall():
    print(f"  completed={r[0]}: {r[1]}")

# Check updated_at if exists
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='browser_profiles' ORDER BY ordinal_position")
cols = [r[0] for r in cur.fetchall()]
print(f"\nAll columns: {cols}")

# Check last_used_at
if 'last_used_at' in cols:
    cur.execute("SELECT id, name, status, last_used_at FROM browser_profiles WHERE status='warming_up' ORDER BY last_used_at DESC NULLS LAST LIMIT 5")
    print("\nLast used warming_up profiles:")
    for r in cur.fetchall():
        print(f"  id={r[0]} name={r[1]} status={r[2]} last_used={r[3]}")

# Check Chrome processes
import subprocess
result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
chrome_count = len([l for l in result.stdout.split('\n') if 'chrome' in l.lower() or 'chromium' in l.lower()])
print(f"\nChrome processes: {chrome_count}")

conn.close()
