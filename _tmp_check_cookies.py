#!/usr/bin/env python3
"""Check cookie counts across browser profiles."""
import sqlite3
import os
import sys

PROFILES_DIR = "/app/browser_profiles"

results = []
for d in os.listdir(PROFILES_DIR):
    if not d.startswith("Profile-"):
        continue
    cookie_path = os.path.join(PROFILES_DIR, d, "Default", "Cookies")
    if not os.path.exists(cookie_path):
        continue
    size = os.path.getsize(cookie_path)
    mtime = os.path.getmtime(cookie_path)
    try:
        conn = sqlite3.connect(cookie_path)
        tables = [t[0] for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        cookie_count = 0
        if "cookies" in tables:
            cookie_count = conn.execute("SELECT count(*) FROM cookies").fetchone()[0]
        # Check domains
        domains = []
        if cookie_count > 0:
            domains = [r[0] for r in conn.execute("SELECT DISTINCT host_key FROM cookies LIMIT 20").fetchall()]
        conn.close()
        results.append((d, size, cookie_count, mtime, tables, domains))
    except Exception as e:
        results.append((d, size, -1, mtime, [], [str(e)]))

# Sort by cookie count descending
results.sort(key=lambda x: x[2], reverse=True)

print(f"Total profile dirs with Cookies file: {len(results)}")
print()
print("=== Top 30 by cookie count ===")
for name, size, count, mtime, tables, domains in results[:30]:
    print(f"  {name}: {count} cookies, {size}b, tables={tables}")
    if domains:
        print(f"    domains: {domains[:10]}")

print()
print("=== Cookie distribution ===")
ranges = [(0, 0), (1, 10), (11, 50), (51, 100), (101, 500), (501, 10000)]
for lo, hi in ranges:
    c = sum(1 for _, _, cnt, _, _, _ in results if lo <= cnt <= hi)
    print(f"  {lo}-{hi} cookies: {c} profiles")

# Also check Local Storage sizes
print()
print("=== Local Storage (top 10 by size) ===")
ls_results = []
for d in os.listdir(PROFILES_DIR):
    if not d.startswith("Profile-"):
        continue
    ls_path = os.path.join(PROFILES_DIR, d, "Default", "Local Storage", "leveldb")
    if os.path.exists(ls_path):
        total = sum(os.path.getsize(os.path.join(ls_path, f)) for f in os.listdir(ls_path) if os.path.isfile(os.path.join(ls_path, f)))
        ls_results.append((d, total))
ls_results.sort(key=lambda x: x[1], reverse=True)
for name, total in ls_results[:10]:
    print(f"  {name}: {total}b LocalStorage")

# Check recently modified (actively warming profiles)
print()
print("=== 10 most recently modified cookie files ===")
results.sort(key=lambda x: x[3], reverse=True)
for name, size, count, mtime, tables, domains in results[:10]:
    from datetime import datetime
    mt = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
    print(f"  {name}: {count} cookies, modified={mt}, {size}b")
    if domains:
        print(f"    domains: {domains[:10]}")
