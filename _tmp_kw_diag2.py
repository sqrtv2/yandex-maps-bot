#!/usr/bin/env python3
from app.database import SessionLocal
from app.models import Task
from app.models.yandex_search_target import YandexSearchTarget
from app.models.search_position_history import SearchPositionHistory
from app.models.profile_search_visit import ProfileSearchVisit
from app.models.browser_profile import BrowserProfile as Profile
from datetime import datetime
from tasks.yandex_search import _calculate_keyword_clicks
from app.models.keyword_frequency import KeywordFrequency

db = SessionLocal()
target = db.query(YandexSearchTarget).filter(YandexSearchTarget.id == 2).first()
keywords = target.get_active_keywords_list()
today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

# Freq weights
freq_records = db.query(KeywordFrequency).filter(KeywordFrequency.target_id == 2).all()
exact_freqs = {}
for fr in freq_records:
    if fr.freq_exact and fr.freq_exact > 0:
        exact_freqs[fr.keyword] = fr.freq_exact
avg_freq = sum(exact_freqs.values()) / len(exact_freqs) if exact_freqs else 1
freq_weights = {}
for fkw, fval in exact_freqs.items():
    raw = fval / avg_freq if avg_freq > 0 else 1.0
    freq_weights[fkw] = max(0.7, min(1.5, raw))

# Pending per keyword
pending_tasks = db.query(Task).filter(
    Task.task_type == 'yandex_search',
    Task.status.in_(['pending', 'in_progress'])
).all()
pending_kw = {}
for t in pending_tasks:
    p = t.parameters or {}
    if p.get('target_id') == 2:
        k = p.get('keyword', '?')
        pending_kw[k] = pending_kw.get(k, 0) + 1

# Per-keyword budget
sr = target.success_rate if target.total_visits >= 10 else 100.0
print(f"{'keyword':25s} | {'cpd':>4s} | {'hist':>4s} | {'pend':>4s} | {'eff_done':>8s} | {'remain':>6s} | {'phase':10s} | reason")
total_budget = 0
for kw in keywords:
    fw = freq_weights.get(kw, 1.0)
    calc = _calculate_keyword_clicks(db, 2, kw, target_success_rate=sr, freq_weight=fw)
    pend = pending_kw.get(kw, 0)
    eff_done = calc["today_done"] + pend
    remaining = max(0, calc["clicks_per_day"] - eff_done)
    total_budget += remaining
    print(f"{kw:25s} | {calc['clicks_per_day']:4d} | {calc['today_done']:4d} | {pend:4d} | {eff_done:8d} | {remaining:6d} | {calc['phase']:10s} | {calc['reason']}")

print(f"\nTotal budget (sum remaining): {total_budget}")

# Profile availability
all_profiles = db.query(Profile.id).filter(Profile.warmup_stage == 'warmed').all()
all_ids = [r[0] for r in all_profiles]

busy_rows = db.query(Task.profile_id).filter(
    Task.task_type == 'yandex_search',
    Task.status.in_(['pending', 'in_progress']),
    Task.profile_id.isnot(None),
).distinct().all()
busy_ids = set(r[0] for r in busy_rows)

already_clicked_rows = db.query(ProfileSearchVisit.profile_id).filter(
    ProfileSearchVisit.search_target_id == 2,
    ProfileSearchVisit.status == 'completed',
).all()
already_clicked = set(r[0] for r in already_clicked_rows)

free = [p for p in all_ids if p not in busy_ids and p not in already_clicked]
print(f"\nWarmed profiles: {len(all_ids)}")
print(f"Busy (pending/in_progress): {len(busy_ids)}")
print(f"Already clicked confitrade: {len(already_clicked)}")
print(f"Free for confitrade: {len(free)}")

# Also check scheduler logs
print(f"\nPending tasks total for confitrade: {sum(pending_kw.values())}")
for kw, cnt in sorted(pending_kw.items(), key=lambda x: -x[1]):
    print(f"  {kw:25s} pending={cnt}")

db.close()
