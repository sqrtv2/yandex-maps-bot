"""Query DB for available profiles and targets."""
import os
os.environ['YANDEX_BOT_DATABASE_URL'] = 'postgresql://postgres:password@127.0.0.1:15432/yandex_maps_bot'
os.environ['YANDEX_BOT_REDIS_HOST'] = '127.0.0.1'
os.environ['YANDEX_BOT_REDIS_PORT'] = '16379'

from app.database import get_db_session
from app.models import BrowserProfile
from app.models.yandex_search_target import YandexSearchTarget

with get_db_session() as db:
    targets = db.query(YandexSearchTarget).filter(YandexSearchTarget.is_active == True).all()
    print(f"Active targets: {len(targets)}")
    for t in targets[:5]:
        kw_raw = t.keywords
        print(f"  ID={t.id} domain={t.domain} kw_type={type(kw_raw).__name__}")
        if isinstance(kw_raw, str):
            print(f"    raw[:200]: {kw_raw[:200]}")
            import json
            try:
                parsed = json.loads(kw_raw)
                print(f"    parsed type: {type(parsed).__name__}, len: {len(parsed)}")
                for k in parsed[:3]:
                    print(f"    - {k}")
            except:
                print(f"    NOT valid JSON")
        elif isinstance(kw_raw, list):
            print(f"    list len: {len(kw_raw)}")
            for k in kw_raw[:3]:
                print(f"    - {repr(k)[:100]}")

    # Warmed profiles (those used for search)
    profiles = db.query(BrowserProfile).filter(
        BrowserProfile.is_active == True,
        BrowserProfile.status == 'warmed'
    ).order_by(BrowserProfile.id.desc()).limit(5).all()
    print(f"")
    print(f"Warmed profiles: {len(profiles)}")
    for p in profiles:
        print(f"  ID={p.id} name={p.name} proxy={p.proxy_host}:{p.proxy_port} ua={p.user_agent[:50] if p.user_agent else 'none'}")
