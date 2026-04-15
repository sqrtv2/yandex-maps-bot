"""Debug: check what inputs exist on Yandex Maps page."""
import sys
import os
import time
import logging

sys.path.insert(0, '/app')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')

from app.database import get_db_session
from app.models import BrowserProfile
from core.browser_manager import BrowserManager
from core.proxy_manager import ProxyManager
from core.profile_generator import ProfileGenerator
from core.playwright_driver import By


def main():
    # Get profile
    with get_db_session() as db:
        p = db.query(BrowserProfile).filter(
            BrowserProfile.warmup_completed == True,
            BrowserProfile.is_active == True
        ).order_by(BrowserProfile.last_used_at.asc().nullsfirst()).first()

        pdata = {
            'name': p.name,
            'user_agent': p.user_agent,
            'viewport_width': p.viewport_width,
            'viewport_height': p.viewport_height,
            'timezone': p.timezone,
            'language': p.language,
            'proxy_host': p.proxy_host,
            'proxy_port': p.proxy_port,
            'proxy_username': p.proxy_username,
            'proxy_password': p.proxy_password,
            'proxy_type': p.proxy_type or 'http',
            'platform': p.platform,
            'is_mobile': p.is_mobile or False,
        }
        print(f"Profile: {p.name}")

    # Proxy
    proxy_data = {
        'host': pdata['proxy_host'],
        'port': pdata['proxy_port'],
        'username': pdata['proxy_username'],
        'password': pdata['proxy_password'],
        'proxy_type': pdata['proxy_type'],
    }

    # Profile data
    pg = ProfileGenerator()
    profile_data = pg.generate_profile(pdata['name'], is_mobile=False)
    profile_data.update({
        'user_agent': pdata['user_agent'],
        'viewport': {'width': pdata['viewport_width'], 'height': pdata['viewport_height']},
        'timezone': pdata['timezone'],
        'language': 'ru-RU',
        'platform': pdata.get('platform') or 'Win32',
        'images_enabled': True,
    })

    bm = BrowserManager()
    bid = bm.create_browser_session(profile_data, proxy_data)
    driver = bm.active_browsers.get(bid)
    print(f"Browser: {bid}")

    try:
        # Navigate
        bm.navigate_to_url(bid, 'https://yandex.ru/maps', timeout=30)
        time.sleep(5)
        print(f"Title: {driver.title}")
        print(f"URL: {driver.current_url}")

        # Try to find inputs
        all_inputs = driver.find_elements(By.CSS_SELECTOR, 'input')
        print(f"Total inputs: {len(all_inputs)}")
        for i, inp in enumerate(all_inputs[:15]):
            try:
                ph = inp.get_attribute('placeholder') or ''
                cl = inp.get_attribute('class') or ''
                tp = inp.get_attribute('type') or ''
                vis = inp.is_displayed()
                sz = inp.size
                h = sz.get('height', 0) if isinstance(sz, dict) else 0
                print(f"  [{i}] class='{cl[:80]}' type='{tp}' placeholder='{ph[:50]}' visible={vis} h={h}")
            except Exception as e:
                print(f"  [{i}] error: {e}")

        # Also try textareas
        textareas = driver.find_elements(By.CSS_SELECTOR, 'textarea')
        print(f"Total textareas: {len(textareas)}")

        # Try broader search
        for sel in [
            "input.input__control",
            "[class*='search'] input",
            "[class*='Search'] input",
            "[role='searchbox']",
            "[aria-label*='Поиск']",
            "[placeholder*='Поиск']",
            "[placeholder*='Найд']",
            "header input",
            ".sidebar-panel-header input",
        ]:
            elems = driver.find_elements(By.CSS_SELECTOR, sel)
            if elems:
                print(f"  FOUND {len(elems)} with selector: {sel}")
            else:
                print(f"  nothing: {sel}")

        # Dump a snippet of page source for hints
        src = driver.page_source
        # Find any input-related HTML
        import re
        inputs_html = re.findall(r'<input[^>]{0,300}>', src, re.IGNORECASE)
        print(f"\nRaw <input> tags in page ({len(inputs_html)}):")
        for ih in inputs_html[:10]:
            print(f"  {ih[:200]}")

    finally:
        bm.close_browser_session(bid)
        print("Done")


if __name__ == "__main__":
    main()
