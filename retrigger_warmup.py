#!/usr/bin/env python3
"""Re-trigger warmup for failed/stuck profiles (5101-5200)."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.database import get_db_session
from app.models.browser_profile import BrowserProfile
from tasks.warmup import warmup_profile_task

with get_db_session() as db:
    # Find profiles that are NOT warmed
    failed = db.query(BrowserProfile).filter(
        BrowserProfile.id.between(5101, 5200),
        BrowserProfile.status != 'warmed'
    ).all()
    
    ids = [p.id for p in failed]
    print(f"Re-triggering warmup for {len(ids)} profiles: {ids}")
    
    # Reset to warming_up
    db.query(BrowserProfile).filter(
        BrowserProfile.id.in_(ids)
    ).update({"status": "warming_up"}, synchronize_session=False)
    db.commit()
    
    for pid in ids:
        try:
            r = warmup_profile_task.delay(pid, 30)
            print(f"  Profile {pid}: task {r.id}")
        except Exception as e:
            print(f"  Profile {pid}: FAILED - {e}")

print("Done!")
