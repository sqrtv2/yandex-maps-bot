#!/usr/bin/env python3
from app.database import SessionLocal
from app.models.browser_profile import BrowserProfile
from collections import Counter

db = SessionLocal()

# Status distribution
statuses = Counter()
for p in db.query(BrowserProfile.status).all():
    statuses[str(p[0])] += 1
print("=== Profile status distribution ===")
for s, c in statuses.most_common():
    print(f"  {s:20s}: {c}")

# Warmed profiles
warmed = db.query(BrowserProfile).filter(
    BrowserProfile.warmup_completed == True,
    BrowserProfile.is_active == True,
    BrowserProfile.status == 'warmed',
).all()
print(f"\nWarmed profiles (ready for search): {len(warmed)}")

# How many of these already clicked each target
from app.models.profile_search_visit import ProfileSearchVisit
from app.models.yandex_search_target import YandexSearchTarget

warmed_ids = set(p.id for p in warmed)
targets = db.query(YandexSearchTarget).all()

print("\n=== Profile usage per target ===")
for t in targets:
    clicked_rows = db.query(ProfileSearchVisit.profile_id).filter(
        ProfileSearchVisit.search_target_id == t.id,
        ProfileSearchVisit.status == 'completed',
    ).all()
    clicked = set(r[0] for r in clicked_rows)
    warmed_clicked = clicked & warmed_ids
    warmed_free = warmed_ids - clicked
    print(f"  {t.domain:30s} | clicked={len(warmed_clicked):3d} | free={len(warmed_free):3d} / {len(warmed)}")

# Warming pipeline
warming = db.query(BrowserProfile).filter(BrowserProfile.status == 'warming').count()
new_count = db.query(BrowserProfile).filter(BrowserProfile.status == 'new').count()
print(f"\nPipeline: warming={warming}, new={new_count}")

db.close()
