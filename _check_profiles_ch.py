#!/usr/bin/env python3
from app.database import SessionLocal
from app.models import BrowserProfile

db = SessionLocal()
profiles = db.query(BrowserProfile).filter(BrowserProfile.is_active == True).limit(10).all()

has_ch = 0
no_ch = 0

for p in profiles:
    fp = p.fingerprint or {}
    ch = fp.get("client_hints")
    plat = fp.get("platform", "MISSING")
    if ch:
        has_ch += 1
        brands = ch.get("brands", [])
        ch_platform = ch.get("platform", "MISSING")
        ch_pv = ch.get("platformVersion", "MISSING")
        print(f"Profile {p.name}: HAS client_hints, platform={plat}")
        print(f"  brands={brands}")
        print(f"  ch_platform={ch_platform}, platformVersion={ch_pv}")
    else:
        no_ch += 1
        print(f"Profile {p.name}: NO client_hints, platform={plat}")

# Count totals
total = db.query(BrowserProfile).filter(BrowserProfile.is_active == True).count()
print(f"\n=== TOTALS: {total} active profiles ===")
# Check how many have client_hints
from sqlalchemy import text
# SQLite JSON
try:
    with_ch = db.execute(text(
        "SELECT COUNT(*) FROM browser_profiles WHERE is_active = 1 AND json_extract(fingerprint, '$.client_hints') IS NOT NULL"
    )).scalar()
    without_ch = db.execute(text(
        "SELECT COUNT(*) FROM browser_profiles WHERE is_active = 1 AND json_extract(fingerprint, '$.client_hints') IS NULL"
    )).scalar()
    print(f"  With client_hints: {with_ch}")
    print(f"  Without client_hints: {without_ch}")
except Exception:
    # PostgreSQL
    try:
        with_ch = db.execute(text(
            "SELECT COUNT(*) FROM browser_profiles WHERE is_active = true AND fingerprint::jsonb ? 'client_hints'"
        )).scalar()
        without_ch = db.execute(text(
            "SELECT COUNT(*) FROM browser_profiles WHERE is_active = true AND NOT (fingerprint::jsonb ? 'client_hints')"
        )).scalar()
        print(f"  With client_hints: {with_ch}")
        print(f"  Without client_hints: {without_ch}")
    except Exception as e:
        print(f"  Could not count: {e}")

db.close()
