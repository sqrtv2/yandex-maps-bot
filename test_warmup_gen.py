"""Test: generate warmup sites for 5 profiles."""
from app.database import get_db_session
from app.models import BrowserProfile
from core.ai_persona_generator import generate_personas, generate_warmup_sites
from sqlalchemy.orm.attributes import flag_modified

with get_db_session() as db:
    profiles = db.query(BrowserProfile).filter(
        BrowserProfile.persona_data == None
    ).limit(5).all()

    print(f"Processing {len(profiles)} profiles...")

    personas = generate_personas(count=5)
    print(f"Generated {len(personas)} personas")

    for i, profile in enumerate(profiles):
        if i >= len(personas):
            break
        persona = personas[i]
        persona["assigned_profile"] = profile.name

        ws = generate_warmup_sites(persona)
        persona["warmup_sites"] = ws.get("warmup_sites", [])
        persona["extra_search_queries"] = ws.get("extra_search_queries", [])

        profile.persona_data = persona
        flag_modified(profile, "persona_data")

        ws_count = len(persona["warmup_sites"])
        q_count = len(persona["extra_search_queries"])
        print(f"  {profile.name}: {persona['name']} ({persona['profession']}) -> {ws_count} sites, {q_count} queries")

    db.commit()
    print("Done!")
