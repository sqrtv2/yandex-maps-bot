#!/usr/bin/env python3
"""Reset all 100 profiles to warming_up and re-trigger warmup."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.database import get_db_session
from app.models.browser_profile import BrowserProfile
from tasks.warmup import warmup_profile_task

with get_db_session() as db:
    # Reset ALL 100 profiles
    count = db.query(BrowserProfile).filter(
        BrowserProfile.id.between(5101, 5200)
    ).update({
        "status": "warming_up",
        "warmup_completed": False,
        "warmup_sessions_count": 0,
        "warmup_time_spent": 0,
    }, synchronize_session=False)
    db.commit()
    print(f"Reset {count} profiles to warming_up")

    # Trigger warmup
    ids = list(range(5101, 5201))
    task_count = 0
    for pid in ids:
        try:
            warmup_profile_task.delay(pid, 30)
            task_count += 1
        except Exception as e:
            print(f"  Failed profile {pid}: {e}")

    print(f"Started {task_count} warmup tasks")
