#!/usr/bin/env python3
"""Debug: reproduce EXACT production flow - referrer timeout then ya.ru"""
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
import time, os

profile_dir = "./browser_profiles/Profile-Test-Debug2"
os.makedirs(profile_dir, exist_ok=True)

lock = os.path.join(profile_dir, "SingletonLock")
if os.path.exists(lock) or os.path.islink(lock):
    os.remove(lock)

p = sync_playwright().start()

proxy_config = {
    "server": "http://msk-2.lte-center.ru:4087",
    "username": "sqrtv2",
    "password": "21607141",
}

launch_args = [
    "--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--disable-infobars", "--no-first-run", "--no-default-browser-check",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
]

ctx = p.chromium.launch_persistent_context(
    user_data_dir=profile_dir,
    headless=True,
    args=launch_args,
    proxy=proxy_config,
    viewport={"width": 1366, "height": 768},
    locale="ru-RU",
    timezone_id="Europe/Moscow",
    ignore_https_errors=True,
)

stealth = Stealth(
    chrome_app=True, chrome_csi=True, chrome_load_times=True,
    hairline=True, media_codecs=True, error_prototype=True,
    navigator_vendor=True, sec_ch_ua=True,
    navigator_webdriver=False, navigator_hardware_concurrency=False,
    navigator_languages=False, navigator_platform=False,
    navigator_plugins=False, navigator_permissions=False,
    navigator_user_agent=False, chrome_runtime=False,
    iframe_content_window=False, webgl_vendor=False,
)
stealth.apply_stealth_sync(ctx)

page = ctx.pages[0] if ctx.pages else ctx.new_page()

# Production does set_page_load_timeout(40) which maps to 40s
# But referrer uses timeout=30s

# === SCENARIO A: referrer times out (like production) ===
print("=== Scenario A: referrer timeout -> window.stop -> ya.ru ===")
try:
    page.goto("https://mail.ru", timeout=15000)  # shorter timeout to force timeout
    print(f"  mail.ru loaded OK: {page.title()}")
except Exception as e:
    print(f"  mail.ru timed out (expected): {str(e)[:80]}")
    try:
        page.evaluate("window.stop()")
        print("  window.stop() called")
    except Exception as e2:
        print(f"  window.stop() failed: {e2}")

time.sleep(1)

# Now go to ya.ru - this is what fails in production
print("\n  Going to ya.ru...")
try:
    page.goto("https://ya.ru", timeout=40000)
    time.sleep(4)
    title = page.title()
    print(f"  ya.ru Title: '{title}'")
    if not title.strip():
        print("  *** REPRODUCED! Title is empty after referrer timeout ***")
        html = page.evaluate("() => document.documentElement.outerHTML")
        print(f"  HTML length: {len(html)}")
        print(f"  HTML[:500]: {html[:500]}")
    else:
        print("  OK - title present")
except Exception as e:
    print(f"  ya.ru FAILED: {e}")

# === SCENARIO B: referrer loads OK then ya.ru ===
print("\n=== Scenario B: referrer OK (dzen.ru - fast) -> ya.ru ===")
page2 = ctx.new_page()
try:
    page2.goto("https://dzen.ru", timeout=15000)
    time.sleep(2)
    print(f"  dzen.ru Title: '{page2.title()}'")
except Exception as e:
    print(f"  dzen.ru: {str(e)[:80]}")
    try:
        page2.evaluate("window.stop()")
    except: pass

time.sleep(1)
try:
    page2.goto("https://ya.ru", timeout=40000)
    time.sleep(4)
    title2 = page2.title()
    print(f"  ya.ru Title: '{title2}'")
    if not title2.strip():
        print("  *** Empty title after dzen referrer ***")
    else:
        print("  OK - title present")
except Exception as e:
    print(f"  ya.ru FAILED: {e}")

# === SCENARIO C: direct ya.ru ===
print("\n=== Scenario C: direct ya.ru (no referrer) ===")
page3 = ctx.new_page()
try:
    page3.goto("https://ya.ru", timeout=40000)
    time.sleep(4)
    title3 = page3.title()
    print(f"  ya.ru Title: '{title3}'")
except Exception as e:
    print(f"  ya.ru FAILED: {e}")

ctx.close()
p.stop()
print("\nDone.")
