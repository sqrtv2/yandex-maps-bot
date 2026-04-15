"""
Open a browser with one of our profiles and navigate to browserscan.net.
Keeps the browser open for manual inspection.

Requires SSH tunnels:
  ssh -f -N -L 15432:172.18.0.2:5432 -L 16379:172.18.0.4:6379 root@88.99.146.218
"""
import os, sys, json, time

os.environ['YANDEX_BOT_DATABASE_URL'] = 'postgresql://postgres:password@127.0.0.1:15432/yandex_maps_bot'
os.environ['YANDEX_BOT_REDIS_HOST'] = '127.0.0.1'
os.environ['YANDEX_BOT_REDIS_PORT'] = '16379'
os.environ['YANDEX_BOT_BROWSER_HEADLESS'] = 'false'
os.environ['YANDEX_BOT_DEBUG'] = 'true'

sys.path.insert(0, os.path.dirname(__file__))

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

from app.database import get_db_session
from app.models import BrowserProfile
from core.browser_manager import BrowserManager
from core.profile_generator import ProfileGenerator

def main():
    # Pick a warmed profile from DB
    with get_db_session() as db:
        profile = db.query(BrowserProfile).filter(
            BrowserProfile.is_active == True,
            BrowserProfile.warmup_completed == True,
        ).first()

        if not profile:
            # Fallback: any active profile
            profile = db.query(BrowserProfile).filter(
                BrowserProfile.is_active == True,
            ).first()

        if not profile:
            print("No profiles found in DB!")
            return

        print(f"Using profile: {profile.name} (id={profile.id}, stage={profile.warmup_stage})")
        print(f"  UA: {profile.user_agent[:80]}...")
        print(f"  Platform: {profile.platform}")
        print(f"  Viewport: {profile.viewport_width}x{profile.viewport_height}")
        print(f"  Mobile: {profile.is_mobile}")

        # Build profile_data
        pg = ProfileGenerator()
        profile_data = pg.generate_profile(profile.name, is_mobile=profile.is_mobile)

        # Override with stored DB values
        profile_data['user_agent'] = profile.user_agent
        profile_data['viewport'] = {
            'width': profile.viewport_width,
            'height': profile.viewport_height,
        }
        profile_data['timezone'] = profile.timezone or 'Europe/Moscow'
        profile_data['language'] = profile.language or 'ru-RU'
        profile_data['platform'] = profile.platform or 'Win32'

        # WebGL from DB
        if profile.webgl_fingerprint:
            try:
                wgl = json.loads(profile.webgl_fingerprint) if isinstance(profile.webgl_fingerprint, str) else profile.webgl_fingerprint
                if wgl and 'unmaskedVendor' in wgl:
                    profile_data['webgl_fingerprint'] = wgl
            except:
                pass

        # Screen fingerprint from DB
        if profile.screen_fingerprint:
            sf = profile.screen_fingerprint if isinstance(profile.screen_fingerprint, dict) else json.loads(profile.screen_fingerprint)
            for k in ('screen', 'css_media', 'feature_flags', 'audio_properties', 'speech_voices', 'sensor',
                      'connection_info', 'storage_quota', 'heap_size', 'system_colors',
                      'system_fonts', 'codecs', 'keyboard_layout', 'fonts',
                      'hardware_concurrency', 'device_memory', 'max_touch_points', 'do_not_track',
                      'webgpu_fingerprint'):
                if k in sf:
                    profile_data[k] = sf[k]

    # Launch browser
    bm = BrowserManager()
    bid = None
    try:
        bid = bm.create_browser_session(profile_data, None)
        driver = bm.active_browsers[bid]

        print("\nOpening https://www.browserscan.net/ru ...")
        driver.get("https://www.browserscan.net/ru")
        time.sleep(3)

        print("\n" + "=" * 60)
        print("Browser is open. Inspect the results on browserscan.net")
        print("Press Enter to close the browser...")
        print("=" * 60)
        input()

    except KeyboardInterrupt:
        print("\nClosing...")
    finally:
        if bid and bid in bm.active_browsers:
            try:
                bm.close_browser_session(bid)
            except:
                pass

if __name__ == '__main__':
    main()
