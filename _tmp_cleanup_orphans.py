#!/usr/bin/env python3
"""Delete orphan browser profile directories that have no matching DB record."""
import os, sys, shutil
sys.path.insert(0, '/app')
from app.database import get_db_session
from app.models.browser_profile import BrowserProfile

PROFILES_DIR = '/app/browser_profiles'

# Get all profile IDs from DB
with get_db_session() as db:
    db_ids = set(p.id for p in db.query(BrowserProfile.id).all())

print(f"DB has {len(db_ids)} profiles")

# Also skip Parser-* dirs (different type)
orphans = []
kept = 0
total_size = 0

for d in sorted(os.listdir(PROFILES_DIR)):
    if not d.startswith('Profile-'):
        continue
    try:
        pid = int(d.split('-', 1)[1])
    except ValueError:
        continue

    if pid in db_ids:
        kept += 1
        continue

    dir_path = os.path.join(PROFILES_DIR, d)
    # Calculate size
    dir_size = 0
    for dirpath, dirnames, filenames in os.walk(dir_path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                dir_size += os.path.getsize(fp)
            except OSError:
                pass
    orphans.append((d, dir_size))
    total_size += dir_size

print(f"Found {len(orphans)} orphan Profile dirs, {total_size / (1024**3):.2f} GB total")
print(f"Keeping {kept} dirs that exist in DB")

# Delete orphans
deleted = 0
errors = 0
for d, size in orphans:
    dir_path = os.path.join(PROFILES_DIR, d)
    try:
        shutil.rmtree(dir_path)
        deleted += 1
    except Exception as e:
        print(f"  ERROR deleting {d}: {e}")
        errors += 1

print(f"\nDeleted {deleted} orphan dirs, {errors} errors")
print(f"Freed ~{total_size / (1024**3):.2f} GB")
