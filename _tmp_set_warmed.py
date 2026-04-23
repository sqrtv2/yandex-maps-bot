#!/usr/bin/env python3
"""
Mark profiles with 50+ cookies as fully warmed.
These profiles already have enough browsing history from previous warmup system.
"""
import sqlite3
import os
import sys

# Add project root to path
sys.path.insert(0, '/app')

from app.database import get_db_session
from app.models.browser_profile import BrowserProfile
from datetime import datetime

PROFILES_DIR = "/app/browser_profiles"
MIN_COOKIES = 50

# Step 1: Find profiles with 50+ cookies on disk
print("Scanning cookie files...")
profile_cookies = {}
for d in os.listdir(PROFILES_DIR):
    if not d.startswith("Profile-"):
        continue
    cookie_path = os.path.join(PROFILES_DIR, d, "Default", "Cookies")
    if not os.path.exists(cookie_path):
        continue
    try:
        conn = sqlite3.connect(cookie_path)
        count = conn.execute("SELECT count(*) FROM cookies").fetchone()[0]
        conn.close()
        if count >= MIN_COOKIES:
            # Extract profile ID from dir name "Profile-XXXX"
            pid = int(d.split("-", 1)[1])
            profile_cookies[pid] = count
    except Exception:
        continue

print(f"Found {len(profile_cookies)} profiles with {MIN_COOKIES}+ cookies")

# Step 2: Update DB - only profiles not yet fully warmed
updated = 0
skipped_already_warmed = 0
skipped_warming = 0

with get_db_session() as db:
    for pid, cookie_count in sorted(profile_cookies.items()):
        profile = db.query(BrowserProfile).filter(BrowserProfile.id == pid).first()
        if not profile:
            continue
        
        if profile.warmup_completed:
            skipped_already_warmed += 1
            continue
        
        # Don't touch profiles actively being warmed right now (in-progress chunk)
        if profile.status == "warming_up":
            skipped_warming += 1
            print(f"  SKIP {pid}: currently warming_up ({cookie_count} cookies)")
            continue
        
        old_stage = profile.warmup_stage
        old_status = profile.status
        
        profile.warmup_completed = True
        profile.status = "warmed"
        profile.warmup_stage = max(profile.warmup_stage, 3)  # MIN_WARMUP_SESSIONS
        if not profile.first_warmup_at:
            profile.first_warmup_at = datetime.utcnow()
        profile.last_used_at = datetime.utcnow()
        
        updated += 1
        print(f"  SET WARMED: Profile-{pid} ({cookie_count} cookies, was stage={old_stage} status={old_status})")
    
    db.commit()

print(f"\nDone: {updated} profiles marked warmed, {skipped_already_warmed} already warmed, {skipped_warming} skipped (warming_up)")
