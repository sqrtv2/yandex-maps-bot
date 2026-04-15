#!/usr/bin/env python3
"""Simulate scheduler keyword budget to understand distribution"""
import math
from app.database import SessionLocal
from app.models.yandex_search_target import YandexSearchTarget
from sqlalchemy import text
from datetime import datetime, timedelta

db = SessionLocal()
target = db.query(YandexSearchTarget).filter(YandexSearchTarget.id == 2).first()
keywords = target.get_active_keywords_list()
print(f"Domain: {target.domain}, visits_per_day={target.visits_per_day}")
print(f"Active keywords: {keywords}")
print()

# Import the real function
import sys
sys.path.insert(0, '/app')
from tasks.yandex_search import _calculate_keyword_clicks

# Load freq weights
from app.models.keyword_frequency import KeywordFrequency
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

# Calculate budgets
keyword_budgets = []
total_budget = 0
for kw in keywords:
    fw = freq_weights.get(kw, 1.0)
    calc = _calculate_keyword_clicks(db, 2, kw, target_success_rate=100.0, freq_weight=fw)
    remaining = max(0, calc["clicks_per_day"] - calc["today_done"])
    keyword_budgets.append({
        "keyword": kw,
        "clicks_per_day": calc["clicks_per_day"],
        "today_done": calc["today_done"],
        "remaining": remaining,
        "phase": calc["phase"],
        "position": calc.get("current_position"),
        "reason": calc["reason"],
        "freq_weight": fw,
    })
    total_budget += remaining
    print(f"  {kw:25s} | cpd={calc['clicks_per_day']:3d} | done={calc['today_done']:3d} | rem={remaining:3d} | fw={fw:.2f} | phase={calc['phase']:10s} | pos={calc.get('current_position') or 'N/A':>5} | {calc['reason']}")

print(f"\nTotal budget (sum remaining): {total_budget}")

# Now check scaling
today_done_total = sum(kb["today_done"] for kb in keyword_budgets)
daily_target = target.visits_per_day
remaining_daily = max(0, daily_target - today_done_total)
print(f"Daily target: {daily_target}, today total done: {today_done_total}, remaining daily: {remaining_daily}")

if remaining_daily > 0 and total_budget < remaining_daily:
    scale = remaining_daily / total_budget if total_budget > 0 else 1.0
    scale = min(scale, 20.0)
    print(f"Scale factor: {scale:.2f}")
    for kb in keyword_budgets:
        if kb["remaining"] > 0:
            scaled = int(math.ceil(kb["remaining"] * scale))
            print(f"  {kb['keyword']:25s} | remaining {kb['remaining']:3d} -> scaled {scaled:3d}")
else:
    print("No scaling needed")

db.close()
