#!/usr/bin/env python3
"""Diagnose keyword click distribution for target_id=2"""
from app.database import SessionLocal
from sqlalchemy import text
from datetime import datetime, timedelta

db = SessionLocal()
today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

active_kws = [
    'волшебные яйца', 'волшебное яйцо', 'яйцо волшебное',
    'бокс свит', 'свит бокс', 'свитбокс', 'sweetbox', 'свит вокс'
]

for kw in active_kws:
    cnt = db.execute(text(
        "SELECT COUNT(*) FROM search_position_history "
        "WHERE search_target_id=2 AND keyword=:kw AND checked_at >= :today"
    ), {"kw": kw, "today": today_start}).scalar()

    since_3d = datetime.utcnow() - timedelta(days=3)
    positions = db.execute(text(
        "SELECT absolute_position FROM search_position_history "
        "WHERE search_target_id=2 AND keyword=:kw AND found=true "
        "AND absolute_position IS NOT NULL AND checked_at >= :since "
        "ORDER BY checked_at DESC LIMIT 5"
    ), {"kw": kw, "since": since_3d}).fetchall()
    pos_list = [r[0] for r in positions]
    avg_pos = sum(pos_list) / len(pos_list) if pos_list else None

    freq = db.execute(text(
        "SELECT freq_exact FROM keyword_frequencies "
        "WHERE target_id=2 AND keyword=:kw"
    ), {"kw": kw}).fetchone()
    freq_val = freq[0] if freq else None

    pos_str = f"{avg_pos:.1f}" if avg_pos else "N/A"
    print(f"{kw:25s} | today={cnt:3d} | avg_pos={pos_str:>6} | freq_exact={freq_val}")

db.close()
