#!/usr/bin/env python3
"""Debug: what exactly does ya.ru return? Test with same setup as production."""
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
import time, os, sys

profile_dir = "./browser_profiles/Profile-Test-Debug"
os.makedirs(profile_dir, exist_ok=True)

# Remove stale locks
lock = os.path.join(profile_dir, "SingletonLock")
if os.path.exists(lock) or os.path.islink(lock):
    os.remove(lock)

p = sync_playwright().start()

# Same proxy as production
proxy_config = {
    "server": "http://msk-2.lte-center.ru:4087",
    "username": "sqrtv2",
    "password": "21607141",
}

# Same launch args as production
launch_args = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--disable-infobars",
    "--no-first-run",
    "--no-default-browser-check",
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
    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    locale="ru-RU",
    timezone_id="Europe/Moscow",
    ignore_https_errors=True,
)

# Apply stealth (same as production)
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

# Apply CDP (same as production)
cdp = ctx.new_cdp_session(page)
cdp.send("Network.enable", {})

# Block analytics (same as production)
blocked = [
    "*mc.yandex.ru*", "*mc.yandex.com*", "*metrika.yandex.ru*",
    "*metrica.yandex.com*", "*cdn.metrika.yandex.net*",
    "*google-analytics.com*", "*googletagmanager.com*",
    "*fonts.googleapis.com*", "*fonts.gstatic.com*",
    "*.woff2*", "*.woff*", "*.ttf*", "*.otf*",
    "*.mp4*", "*.webm*",
]
cdp.send("Network.setBlockedURLs", {"urls": blocked})

# Inject analytics kill JS (same as production)
kill_js = """
(function() {
    if (navigator.sendBeacon) {
        navigator.sendBeacon = function() { return true; };
    }
    window.Ya = window.Ya || {};
    window.Ya.Metrika2 = function() {
        return { reachGoal: function(){}, hit: function(){} };
    };
})();
"""
cdp.send("Page.addScriptToEvaluateOnNewDocument", {"source": kill_js})

# Step 1: Visit referrer (like production does 50% of the time)
print("=== Step 1: Visit referrer (mail.ru) ===")
try:
    page.goto("https://mail.ru", timeout=30000)
    time.sleep(2)
    print(f"  mail.ru Title: '{page.title()}'")
    print(f"  mail.ru URL: {page.url}")
except Exception as e:
    print(f"  mail.ru error: {e}")

# Step 2: Go to ya.ru (same as production)
print("\n=== Step 2: Navigate to ya.ru ===")
try:
    page.goto("https://ya.ru", timeout=40000)
    time.sleep(4)
    title = page.title()
    url = page.url
    print(f"  ya.ru Title: '{title}'")
    print(f"  ya.ru URL: {url}")
    
    if not title.strip():
        print("\n  *** REPRODUCED: Title is empty! ***")
        # Get page content for debug
        html = page.evaluate("() => document.documentElement.outerHTML")
        print(f"  HTML length: {len(html)}")
        print(f"  HTML first 1000 chars:\n{html[:1000]}")
        
        # Check what document.title returns
        doc_title = page.evaluate("() => document.title")
        print(f"\n  document.title via evaluate: '{doc_title}'")
        
        # Check if page has any content
        body_text = page.evaluate("() => document.body ? document.body.innerText.substring(0, 500) : 'NO BODY'")
        print(f"  body.innerText: '{body_text}'")
    else:
        print("  OK! Title is present.")
except Exception as e:
    print(f"  ya.ru error: {e}")

# Step 3: Try without referrer — fresh page
print("\n=== Step 3: Direct ya.ru (new page, no referrer) ===")
page2 = ctx.new_page()
try:
    page2.goto("https://ya.ru", timeout=40000)
    time.sleep(4)
    title2 = page2.title()
    print(f"  ya.ru Title: '{title2}'")
    print(f"  ya.ru URL: {page2.url}")
    if not title2.strip():
        html2 = page2.evaluate("() => document.documentElement.outerHTML")
        print(f"  HTML first 1000 chars:\n{html2[:1000]}")
except Exception as e:
    print(f"  ya.ru direct error: {e}")

# Step 4: Try yandex.ru instead
print("\n=== Step 4: Try yandex.ru ===")
try:
    page.goto("https://yandex.ru", timeout=40000)
    time.sleep(4)
    title3 = page.title()
    print(f"  yandex.ru Title: '{title3}'")
    print(f"  yandex.ru URL: {page.url}")
except Exception as e:
    print(f"  yandex.ru error: {e}")

ctx.close()
p.stop()
print("\nDone.")
