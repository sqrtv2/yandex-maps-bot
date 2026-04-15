#!/usr/bin/env python3
from app.database import SessionLocal
from app.models.yandex_search_target import YandexSearchTarget
from app.models.search_position_history import SearchPositionHistory
from app.models import Task
from app.models.profile_search_visit import ProfileSearchVisit
from datetime import datetime

db = SessionLocal()
target = db.query(YandexSearchTarget).filter(YandexSearchTarget.domain == 'povoenke.ru').first()
if not target:
    print('Target not found')
    exit()

print(f"Target: id={target.id}, domain={target.domain}")
print(f"visits_per_day={target.visits_per_day}")
keywords = target.get_active_keywords_list()
disabled = target.get_disabled_keywords_set()
print(f"Active keywords: {len(keywords)}, disabled: {len(disabled)}")

today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

# Total today from history
total_today = db.query(SearchPositionHistory).filter(
    SearchPositionHistory.search_target_id == target.id,
    SearchPositionHistory.checked_at >= today_start
).count()
print(f"Total clicks today (history): {total_today}")

# Pending/in_progress tasks
pending = db.query(Task).filter(
    Task.task_type == 'yandex_search',
    Task.status.in_(['pending', 'in_progress'])
).all()
target_pending = [t for t in pending if (t.parameters or {}).get('target_id') == target.id]
print(f"Pending/in_progress tasks: {len(target_pending)}")

# Failed/error tasks today
failed_today = db.query(Task).filter(
    Task.task_type == 'yandex_search',
    Task.status.in_(['failed', 'error']),
    Task.updated_at >= today_start,
).all()
target_failed = [t for t in failed_today if (t.parameters or {}).get('target_id') == target.id]
print(f"Failed/error tasks today: {len(target_failed)}")

# Completed tasks today
completed_today = db.query(Task).filter(
    Task.task_type == 'yandex_search',
    Task.status == 'completed',
    Task.updated_at >= today_start,
).all()
target_completed = [t for t in completed_today if (t.parameters or {}).get('target_id') == target.id]
print(f"Completed tasks today: {len(target_completed)}")

# Profile availability
already_clicked_rows = db.query(ProfileSearchVisit.profile_id).filter(
    ProfileSearchVisit.search_target_id == target.id,
    ProfileSearchVisit.status == 'completed',
).all()
already_clicked = set(r[0] for r in already_clicked_rows)
print(f"Profiles already clicked povoenke: {len(already_clicked)}")

# Per keyword stats
print(f"\n{'keyword':45s} | done | pend")
for kw in keywords:
    done = db.query(SearchPositionHistory).filter(
        SearchPositionHistory.search_target_id == target.id,
        SearchPositionHistory.keyword == kw,
        SearchPositionHistory.checked_at >= today_start
    ).count()
    pk = sum(1 for t in target_pending if (t.parameters or {}).get('keyword') == kw)
    print(f"  {kw:43s} | {done:4d} | {pk:4d}")

# Check last scheduler log for this target
print("\n--- Recent failed task errors ---")
for t in target_failed[:5]:
    p = t.parameters or {}
    print(f"  kw={p.get('keyword','?'):30s} error={str(t.error_message or '')[:80]}")

db.close()
