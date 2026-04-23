#!/usr/bin/env python3
import sqlite3, os, sys
sys.path.insert(0, '/app')
from app.database import get_db_session
from app.models.browser_profile import BrowserProfile

PROFILES_DIR = '/app/browser_profiles'

# Scan disk
disk_cookies = {}
for d in os.listdir(PROFILES_DIR):
    if not d.startswith('Profile-'):
        continue
    cookie_path = os.path.join(PROFILES_DIR, d, 'Default', 'Cookies')
    if not os.path.exists(cookie_path):
        continue
    try:
        conn = sqlite3.connect(cookie_path)
        count = conn.execute('SELECT count(*) FROM cookies').fetchone()[0]
        conn.close()
        pid = int(d.split('-', 1)[1])
        disk_cookies[pid] = count
    except:
        continue

# Check DB status for these profiles
with get_db_session() as db:
    all_profiles = {p.id: p for p in db.query(BrowserProfile).all()}

    # 101-500 range
    profiles_101_500 = [(pid, c) for pid, c in disk_cookies.items() if 101 <= c <= 500]
    profiles_501 = [(pid, c) for pid, c in disk_cookies.items() if c > 500]

    print(f"=== 101-500 cookies: {len(profiles_101_500)} profiles ===")
    in_db = 0
    not_in_db = 0
    by_status = {}
    by_active = {True: 0, False: 0}
    for pid, count in sorted(profiles_101_500, key=lambda x: -x[1]):
        p = all_profiles.get(pid)
        if p:
            in_db += 1
            key = f"{p.status} active={p.is_active} warmed={p.warmup_completed}"
            by_status[key] = by_status.get(key, 0) + 1
            by_active[p.is_active] += 1
        else:
            not_in_db += 1

    print(f"  In DB: {in_db}, Not in DB (orphan dirs): {not_in_db}")
    print(f"  Active: {by_active[True]}, Inactive: {by_active[False]}")
    print(f"  By status:")
    for k, v in sorted(by_status.items(), key=lambda x: -x[1]):
        print(f"    {k}: {v}")

    # Show some examples
    print(f"\n  Examples (top 10 by cookies):")
    for pid, count in sorted(profiles_101_500, key=lambda x: -x[1])[:10]:
        p = all_profiles.get(pid)
        if p:
            print(f"    Profile-{pid}: {count} cookies, status={p.status}, active={p.is_active}, warmed={p.warmup_completed}, stage={p.warmup_stage}")
        else:
            print(f"    Profile-{pid}: {count} cookies, NOT IN DB")

    print(f"\n=== 501+ cookies: {len(profiles_501)} profiles ===")
    in_db2 = 0
    not_in_db2 = 0
    by_status2 = {}
    by_active2 = {True: 0, False: 0}
    for pid, count in sorted(profiles_501, key=lambda x: -x[1]):
        p = all_profiles.get(pid)
        if p:
            in_db2 += 1
            key = f"{p.status} active={p.is_active} warmed={p.warmup_completed}"
            by_status2[key] = by_status2.get(key, 0) + 1
            by_active2[p.is_active] += 1
        else:
            not_in_db2 += 1

    print(f"  In DB: {in_db2}, Not in DB (orphan dirs): {not_in_db2}")
    print(f"  Active: {by_active2[True]}, Inactive: {by_active2[False]}")
    print(f"  By status:")
    for k, v in sorted(by_status2.items(), key=lambda x: -x[1]):
        print(f"    {k}: {v}")
