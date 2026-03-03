#!/usr/bin/env python3
"""Check profile timestamps for stale detection."""
from app.database import get_db_session
from app.models.browser_profile import BrowserProfile
from app.models.profile_target_visit import ProfileTargetVisit
from sqlalchemy import func
from datetime import datetime, timedelta

with get_db_session() as db:
    # Get profiles that have visits
    profiles_with_visits = (
        db.query(ProfileTargetVisit.profile_id)
        .filter(ProfileTargetVisit.status == "completed")
        .distinct()
        .all()
    )
    pids = [r[0] for r in profiles_with_visits]
    print("Profiles with completed visits:", len(pids))

    if pids:
        profiles = db.query(BrowserProfile).filter(BrowserProfile.id.in_(pids[:20])).all()
        now = datetime.utcnow()
        print("\nSample profiles with visits:")
        for p in profiles:
            age = (now - p.updated_at).total_seconds() / 3600 if p.updated_at else None
            last_used_age = (now - p.last_used_at).total_seconds() / 3600 if p.last_used_at else None
            print("  ID=%d name=%s status=%s updated_at=%s (%.1fh ago) last_used=%s (%s)" % (
                p.id, p.name, p.status,
                p.updated_at, age or 0,
                p.last_used_at,
                ("%.1fh ago" % last_used_age) if last_used_age else "never"
            ))

    # Count by updated_at age
    now = datetime.utcnow()
    for days in [1, 2, 3, 5, 7, 14]:
        cutoff = now - timedelta(days=days)
        cnt = db.query(BrowserProfile).filter(
            BrowserProfile.id.in_(pids),
            BrowserProfile.updated_at < cutoff,
        ).count()
        print("\nProfiles with visits, updated > %d days ago: %d" % (days, cnt))
