#!/usr/bin/env python3
"""Deep analysis of profile visits and cleanup issues."""
from app.database import get_db_session
from app.models.browser_profile import BrowserProfile
from app.models.yandex_target import YandexMapTarget
from app.models.profile_target_visit import ProfileTargetVisit
from sqlalchemy import func
import os

with get_db_session() as db:
    targets = db.query(YandexMapTarget).filter(YandexMapTarget.is_active == True).all()
    target_ids = [t.id for t in targets]
    num_targets = len(target_ids)

    # Distribution: how many targets each profile visited
    visit_counts = (
        db.query(
            ProfileTargetVisit.profile_id,
            func.count(func.distinct(ProfileTargetVisit.target_id))
        )
        .filter(ProfileTargetVisit.target_id.in_(target_ids))
        .group_by(ProfileTargetVisit.profile_id)
        .all()
    )

    # Histogram
    histogram = {}
    for pid, cnt in visit_counts:
        histogram[cnt] = histogram.get(cnt, 0) + 1

    print("=== Visit distribution (out of %d targets) ===" % num_targets)
    for k in sorted(histogram.keys()):
        print("  %d targets visited: %d profiles" % (k, histogram[k]))

    # Check total visits
    total_visits = db.query(ProfileTargetVisit).count()
    print("\nTotal visit records:", total_visits)

    # Check per target visit counts
    print("\n=== Visits per target ===")
    for t in targets:
        cnt = db.query(ProfileTargetVisit).filter(ProfileTargetVisit.target_id == t.id).count()
        print("  Target #%d (%s): %d visits" % (t.id, t.organization_name, cnt))

    # Check disk usage
    profiles_dir = "/app/browser_profiles"
    if os.path.exists(profiles_dir):
        dirs_on_disk = [d for d in os.listdir(profiles_dir) if d.startswith("Profile-")]
        print("\n=== Disk ===")
        print("Profile dirs on disk:", len(dirs_on_disk))

        # Profiles in DB
        db_names = set(p.name for p in db.query(BrowserProfile.name).all())
        disk_names = set(dirs_on_disk)

        orphaned_dirs = disk_names - db_names
        print("Orphaned dirs (on disk but NOT in DB):", len(orphaned_dirs))
        if orphaned_dirs:
            examples = list(orphaned_dirs)[:10]
            print("  Examples:", examples)

        missing_dirs = db_names - disk_names
        print("Missing dirs (in DB but NOT on disk):", len(missing_dirs))
