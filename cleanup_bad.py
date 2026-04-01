#!/usr/bin/env python3
import psycopg2
conn = psycopg2.connect(host="postgres", dbname="yandex_maps_bot", user="postgres", password="password")
cur = conn.cursor()

# Delete profiles without fingerprint data (the bad ones)
cur.execute("DELETE FROM browser_profiles WHERE screen_fingerprint::text NOT LIKE %s", ("%connection_info%",))
deleted = cur.rowcount
conn.commit()
print(f"Deleted {deleted} profiles without fingerprint data")

# Verify remaining
cur.execute("SELECT COUNT(*) FROM browser_profiles")
print(f"Remaining: {cur.fetchone()[0]}")

cur.execute("SELECT COUNT(*) FROM browser_profiles WHERE user_agent LIKE %s", ("%Firefox%",))
print(f"Firefox remaining: {cur.fetchone()[0]}")

cur.execute("SELECT COUNT(*) FROM browser_profiles WHERE user_agent LIKE %s", ("%Chrome%",))
print(f"Chrome remaining: {cur.fetchone()[0]}")

cur.execute("SELECT status, COUNT(*) FROM browser_profiles GROUP BY status ORDER BY COUNT(*) DESC")
for r in cur.fetchall():
    print(f"  {r[0]}: {r[1]}")

cur.execute("SELECT is_mobile, COUNT(*) FROM browser_profiles GROUP BY is_mobile")
for r in cur.fetchall():
    print(f"  is_mobile={r[0]}: {r[1]}")

cur.execute("SELECT warmup_stage, COUNT(*) FROM browser_profiles GROUP BY warmup_stage ORDER BY warmup_stage")
for r in cur.fetchall():
    print(f"  stage {r[0]}: {r[1]}")

conn.close()
