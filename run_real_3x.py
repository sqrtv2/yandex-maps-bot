"""
Run 3 REAL yandex_search_click_task in parallel — visible browsers, no proxy.
Each gets a different profile + keyword from the DB.

Requires SSH tunnels:
  ssh -f -N -o ServerAliveInterval=30 -L 15432:172.18.0.3:5432 -L 16379:172.18.0.2:6379 root@88.99.146.218
"""
import os
import sys
import threading
import time

os.environ['YANDEX_BOT_DATABASE_URL'] = 'postgresql://postgres:password@127.0.0.1:15432/yandex_maps_bot'
os.environ['YANDEX_BOT_REDIS_HOST'] = '127.0.0.1'
os.environ['YANDEX_BOT_REDIS_PORT'] = '16379'
os.environ['YANDEX_BOT_BROWSER_HEADLESS'] = 'false'
os.environ['YANDEX_BOT_DEBUG'] = 'true'

sys.path.insert(0, os.path.dirname(__file__))

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')

from app.database import get_db_session
from app.models import BrowserProfile
from app.models.yandex_search_target import YandexSearchTarget


def get_test_data(count=3):
    """Pick N warmed profiles + keywords from real DB."""
    with get_db_session() as db:
        # Get warmed profiles first, then created
        profiles = db.query(BrowserProfile).filter(
            BrowserProfile.is_active == True,
            BrowserProfile.status == 'warmed'
        ).order_by(BrowserProfile.id.desc()).limit(count).all()

        if len(profiles) < count:
            extra = db.query(BrowserProfile).filter(
                BrowserProfile.is_active == True,
                BrowserProfile.status == 'created'
            ).order_by(BrowserProfile.id.desc()).limit(count - len(profiles)).all()
            profiles.extend(extra)

        if not profiles:
            print("No active profiles!")
            sys.exit(1)

        # Get targets
        target_pov = db.query(YandexSearchTarget).filter(
            YandexSearchTarget.is_active == True,
            YandexSearchTarget.domain == 'povoenke.ru'
        ).first()

        target_eco = db.query(YandexSearchTarget).filter(
            YandexSearchTarget.is_active == True,
            YandexSearchTarget.domain == 'ecoinstrument.ru'
        ).first()

        # Parse keywords
        def parse_kw(target):
            if not target:
                return []
            raw = target.keywords or ""
            return [k.strip() for k in raw.split('\n') if k.strip()]

        kw_pov = parse_kw(target_pov)
        kw_eco = parse_kw(target_eco)

        # Build 3 test configs: 2 for povoenke, 1 for ecoinstrument
        tests = []
        if target_pov and len(kw_pov) > 0:
            tests.append((profiles[0].id, target_pov.id, kw_pov[0]))
        if target_pov and len(kw_pov) > 1 and len(profiles) > 1:
            tests.append((profiles[1].id, target_pov.id, kw_pov[1]))
        if target_eco and len(kw_eco) > 0 and len(profiles) > 2:
            tests.append((profiles[2].id, target_eco.id, kw_eco[0]))

        # Fill remaining with more povoenke keywords
        while len(tests) < min(count, len(profiles)):
            idx = len(tests)
            if target_pov and len(kw_pov) > idx:
                tests.append((profiles[idx].id, target_pov.id, kw_pov[idx]))
            else:
                break

        return tests


def run_task(profile_id, target_id, keyword, thread_num):
    """Run one real search task."""
    print(f"\n[T{thread_num}] Starting: profile={profile_id}, keyword='{keyword}'")
    try:
        from tasks.yandex_search import yandex_search_click_task
        result = yandex_search_click_task.run(
            profile_id=profile_id,
            target_id=target_id,
            keyword=keyword,
            task_id=None,
            search_params={
                'max_search_pages': 5,
                'min_time_on_site': 30,
                'max_time_on_site': 60,
                'no_proxy': True,
            }
        )
        print(f"\n[T{thread_num}] DONE: {result}")
    except Exception as e:
        print(f"\n[T{thread_num}] ERROR: {e}")
        import traceback
        traceback.print_exc()


def main():
    print("=" * 60)
    print("3x SEQUENTIAL real search tasks — NO PROXY, visible browsers")
    print("=" * 60)

    tests = get_test_data(3)
    print(f"\nPrepared {len(tests)} tests:")
    for i, (pid, tid, kw) in enumerate(tests):
        print(f"  [{i+1}] profile={pid}, target={tid}, keyword='{kw}'")

    print(f"\nRunning {len(tests)} tests one by one...")
    for i, (pid, tid, kw) in enumerate(tests):
        run_task(pid, tid, kw, i+1)

    print("\n" + "=" * 60)
    print("ALL 3 TESTS COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
