#!/usr/bin/env python3
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
from app.database import get_db_session
from app.models.browser_profile import BrowserProfile
from sqlalchemy import func

with get_db_session() as db:
    total = db.query(func.count(BrowserProfile.id)).scalar()
    print(f"Total profiles: {total}")
    
    # Firefox profiles
    ff = db.query(BrowserProfile).filter(BrowserProfile.user_agent.like('%Firefox%')).all()
    print(f"Firefox profiles: {len(ff)}")
    
    # Sample Firefox UAs
    for p in ff[:5]:
        print(f"  id={p.id} name={p.name} ua={p.user_agent[:80]}")
        print(f"    platform={p.platform} created={p.created_at}")
    
    # Chrome profiles
    ch = db.query(BrowserProfile).filter(BrowserProfile.user_agent.like('%Chrome%')).count()
    print(f"\nChrome profiles: {ch}")
    
    # Profiles with new fingerprint vectors
    has_new = 0
    no_new = 0
    for p in db.query(BrowserProfile).all():
        sf = p.screen_fingerprint
        if sf and isinstance(sf, dict) and 'connection_info' in sf:
            has_new += 1
        else:
            no_new += 1
    print(f"With new vectors: {has_new}")
    print(f"Without new vectors: {no_new}")
    
    # Who created profiles after our batch?
    after = db.query(BrowserProfile).filter(BrowserProfile.id > 20958).count()
    print(f"\nProfiles created after our batch (id>20958): {after}")
    
    ff_after = db.query(BrowserProfile).filter(
        BrowserProfile.id > 20958,
        BrowserProfile.user_agent.like('%Firefox%')
    ).count()
    print(f"Firefox among them: {ff_after}")
