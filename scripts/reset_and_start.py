#!/usr/bin/env python3
"""Reset stuck profiles and update settings for fast warmup."""
from app.database import get_db_session, set_setting
from app.models import BrowserProfile

# Update DB settings for fast warmup
set_setting('warmup_min_page_time', 2, 'Min page time (seconds)', 'warmup')
set_setting('warmup_max_page_time', 10, 'Max page time (seconds)', 'warmup')
set_setting('warmup_duration_minutes', 2, 'Warmup duration (minutes)', 'warmup')
print('Settings updated: 2-10s per page')

# Reset stuck profiles
with get_db_session() as db:
    stuck = db.query(BrowserProfile).filter(
        BrowserProfile.status.in_(['warming_up', 'error']),
        BrowserProfile.warmup_completed == False
    ).all()
    for p in stuck:
        p.status = 'created'
    db.commit()

    total = db.query(BrowserProfile).count()
    warmed = db.query(BrowserProfile).filter(BrowserProfile.warmup_completed == True).count()
    need_warmup = db.query(BrowserProfile).filter(
        BrowserProfile.warmup_completed == False,
        BrowserProfile.is_active == True
    ).count()
    print(f"Profiles: {total} total, {warmed} warmed, {need_warmup} need warmup")
    print(f"Reset {len(stuck)} stuck profiles")
