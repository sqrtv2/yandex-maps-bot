#!/usr/bin/env python3
"""Check profile cleanup status."""
from app.database import get_db_session
from app.models.browser_profile import BrowserProfile
from app.models.yandex_target import YandexMapTarget
from app.models.profile_target_visit import ProfileTargetVisit
from sqlalchemy import func

with get_db_session() as db:
    # Profile stats
    total = db.query(BrowserProfile).count()
    statuses = db.query(BrowserProfile.status, func.count()).group_by(BrowserProfile.status).all()
    print("Total profiles:", total)
    for s, c in statuses:
        print("  %s: %d" % (s, c))

    # Active targets
    targets = db.query(YandexMapTarget).filter(YandexMapTarget.is_active == True).all()
    target_ids = [t.id for t in targets]
    num_targets = len(target_ids)
    print("\nActive targets:", num_targets)
    for t in targets:
        print("  Target #%d: %s" % (t.id, t.organization_name))

    if num_targets == 0:
        print("\nNo active targets - cleanup skips everything!")
        exit()

    # Visit analysis
    fully_used = (
        db.query(ProfileTargetVisit.profile_id, func.count(func.distinct(ProfileTargetVisit.target_id)))
        .filter(ProfileTargetVisit.target_id.in_(target_ids))
        .group_by(ProfileTargetVisit.profile_id)
        .all()
    )

    fully_done = [pid for pid, cnt in fully_used if cnt >= num_targets]
    partially_done = [(pid, cnt) for pid, cnt in fully_used if cnt < num_targets]

    print("\nProfiles visited ALL %d targets: %d" % (num_targets, len(fully_done)))
    if fully_done:
        print("  First 10 IDs: %s" % str(fully_done[:10]))
    print("Profiles with partial visits: %d" % len(partially_done))

    profiles_with_visits = set(pid for pid, _ in fully_used)
    no_visits = total - len(profiles_with_visits)
    print("Profiles with NO visits: %d" % no_visits)

    # Check if fully_done profiles exist in BrowserProfile table
    if fully_done:
        existing = db.query(BrowserProfile).filter(BrowserProfile.id.in_(fully_done[:20])).all()
        print("\nFully-used profiles still in DB:")
        for p in existing:
            print("  ID=%d name=%s status=%s warmup=%s" % (p.id, p.name, p.status, p.warmup_completed))
