"""
Run the REAL yandex_search_click_task locally with visible browser.
Calls the actual production code, not a test stub.

Requires SSH tunnels:
  ssh -f -N -L 15432:172.18.0.3:5432 -L 16379:172.18.0.2:6379 root@88.99.146.218
"""
import os
import sys

# === ENV SETUP (before any app imports) ===
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


def get_test_data():
    """Pick a warmed profile + target + keyword from the real DB."""
    with get_db_session() as db:
        # Get a warmed profile
        profile = db.query(BrowserProfile).filter(
            BrowserProfile.is_active == True,
            BrowserProfile.status == 'warmed'
        ).order_by(BrowserProfile.id.desc()).first()

        if not profile:
            # Fall back to created
            profile = db.query(BrowserProfile).filter(
                BrowserProfile.is_active == True,
                BrowserProfile.status == 'created'
            ).order_by(BrowserProfile.id.desc()).first()

        if not profile:
            print("No active profiles found!")
            sys.exit(1)

        # Get povoenke.ru target
        target = db.query(YandexSearchTarget).filter(
            YandexSearchTarget.is_active == True,
            YandexSearchTarget.domain == 'povoenke.ru'
        ).first()

        if not target:
            target = db.query(YandexSearchTarget).filter(
                YandexSearchTarget.is_active == True
            ).first()

        if not target:
            print("No active targets found!")
            sys.exit(1)

        # Parse keywords (newline-separated string)
        kw_raw = target.keywords or ""
        keywords = [k.strip() for k in kw_raw.split('\n') if k.strip()]
        
        # Pick first keyword 
        keyword = keywords[0] if keywords else "военная ипотека 2026"

        print(f"Profile: ID={profile.id}, name={profile.name}, status={profile.status}")
        print(f"Target:  ID={target.id}, domain={target.domain}")
        print(f"Keyword: '{keyword}'")
        print(f"Total keywords available: {len(keywords)}")

        return profile.id, target.id, keyword


def main():
    print("=" * 60)
    print("REAL yandex_search_click_task — LOCAL RUN")
    print("Browser: VISIBLE (headless=false)")
    print("=" * 60)

    profile_id, target_id, keyword = get_test_data()

    print(f"\nStarting task...")
    print(f"  profile_id={profile_id}")
    print(f"  target_id={target_id}")
    print(f"  keyword='{keyword}'")
    print("=" * 60)

    # Import and call the real task function directly (not via Celery)
    from tasks.yandex_search import yandex_search_click_task

    # It's a bound Celery task (bind=True), so we call it as task object
    # Using .run() bypasses Celery and calls the function directly 
    # but still passes self (the task instance)
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

    print(f"\n{'=' * 60}")
    print(f"RESULT: {result}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
