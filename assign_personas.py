#!/usr/bin/env python3
"""Assign AI personas to all profiles that don't have one."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import get_db_session
from app.models.browser_profile import BrowserProfile
from core.ai_persona_generator import generate_personas, generate_warmup_sites
from sqlalchemy.orm.attributes import flag_modified

with get_db_session() as db:
    profiles = db.query(BrowserProfile).filter(
        BrowserProfile.persona_data.is_(None)
    ).order_by(BrowserProfile.id).all()
    
    print(f"Found {len(profiles)} profiles without personas")
    if not profiles:
        print("All profiles already have personas!")
        sys.exit(0)
    
    # Generate personas in batches of 10
    personas_pool = []
    needed = len(profiles)
    while len(personas_pool) < needed:
        batch_size = min(10, needed - len(personas_pool))
        print(f"  Generating batch of {batch_size} personas ({len(personas_pool)}/{needed})...")
        batch = generate_personas(count=batch_size)
        personas_pool.extend(batch)
    
    print(f"Generated {len(personas_pool)} personas, assigning to profiles...")
    
    assigned = 0
    warmup_sites_ok = 0
    for i, profile in enumerate(profiles):
        if i >= len(personas_pool):
            break
        
        persona = personas_pool[i]
        persona["assigned_profile"] = profile.name
        
        # Generate warmup sites for this persona
        try:
            ws = generate_warmup_sites(persona)
            persona["warmup_sites"] = ws.get("warmup_sites", [])
            persona["extra_search_queries"] = ws.get("extra_search_queries", [])
            warmup_sites_ok += 1
        except Exception as e:
            print(f"  Warning: warmup sites failed for {profile.name}: {e}")
        
        profile.persona_data = persona
        flag_modified(profile, "persona_data")
        
        # Sync timezone from persona
        if persona.get("timezone"):
            profile.timezone = persona["timezone"]
        
        assigned += 1
        if assigned % 10 == 0:
            db.flush()
            print(f"  Assigned {assigned}/{needed} personas ({warmup_sites_ok} with warmup sites)")
    
    db.commit()
    print(f"\nDone! Assigned {assigned} personas ({warmup_sites_ok} with warmup sites)")
