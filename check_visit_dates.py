#!/usr/bin/env python3
"""Check visit dates and profile reuse patterns."""
from app.database import get_db_session
from app.models.browser_profile import BrowserProfile
from app.models.profile_target_visit import ProfileTargetVisit
from sqlalchemy import func
from datetime import datetime, timedelta

with get_db_session() as db:
    # When were visits recorded?
    visits = db.query(
        func.date(ProfileTargetVisit.visited_at),
        func.count()
    ).group_by(func.date(ProfileTargetVisit.visited_at)).order_by(func.date(ProfileTargetVisit.visited_at)).all()
    
    print("=== Visits by date ===")
    for d, c in visits:
        print("  %s: %d visits" % (d, c))
    
    # How many profiles have visits but can NO LONGER visit any target?
    # (they've visited all targets they can, but not all 9)
    from app.models.yandex_target import YandexMapTarget
    targets = db.query(YandexMapTarget).filter(YandexMapTarget.is_active == True).all()
    target_ids = [t.id for t in targets]
    
    # For each profile with visits, check which targets they haven't visited
    profiles_with_visits = (
        db.query(ProfileTargetVisit.profile_id, func.count(func.distinct(ProfileTargetVisit.target_id)))
        .filter(ProfileTargetVisit.target_id.in_(target_ids))
        .group_by(ProfileTargetVisit.profile_id)
        .all()
    )
    
    print("\n=== Profile visit distribution ===")
    hist = {}
    for pid, cnt in profiles_with_visits:
        hist[cnt] = hist.get(cnt, 0) + 1
    for k in sorted(hist.keys()):
        print("  %d/%d targets: %d profiles" % (k, len(target_ids), hist[k]))
    
    # How many total profiles are warmed but have ZERO visits?
    pids_with_visits = set(pid for pid, _ in profiles_with_visits)
    warmed_no_visits = db.query(BrowserProfile).filter(
        BrowserProfile.warmup_completed == True,
        ~BrowserProfile.id.in_(pids_with_visits)
    ).count()
    print("\nWarmed profiles with ZERO map visits:", warmed_no_visits)
    
    # Total warmed
    total_warmed = db.query(BrowserProfile).filter(BrowserProfile.warmup_completed == True).count()
    print("Total warmed profiles:", total_warmed)
    
    # Created at distribution
    print("\n=== Profiles by creation date ===")
    created = db.query(
        func.date(BrowserProfile.created_at),
        func.count()
    ).group_by(func.date(BrowserProfile.created_at)).order_by(func.date(BrowserProfile.created_at)).all()
    for d, c in created:
        print("  %s: %d profiles" % (d, c))
