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

deleted = 0
kept = 0
errors = 0

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
    try:
        shutil.rmtree(dir_path)
        deleted += 1
        if deleted % 100 == 0:
            print(f"  ...deleted {deleted}")
    except Exception as e:
        print(f"  ERROR {d}: {e}")
        errors += 1

print(f"Done: deleted {deleted}, kept {kept}, errors {errors}")
