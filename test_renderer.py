#!/usr/bin/env python3
"""Test: reproduce Browser renderer dead issue with full stack."""
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
import time, os, sys

profile_dir = "./browser_profiles/Profile-11486"
proxy_config = {
    "server": "http://msk-2.lte-center.ru:4087",
    "username": "sqrtv2",
    "password": "21607141",
}

lock = os.path.join(profile_dir, "SingletonLock")
if os.path.exists(lock) or os.path.islink(lock):
    os.remove(lock)

p = sync_playwright().start()

ctx = p.chromium.launch_persistent_context(
    user_data_dir=profile_dir,
    headless=True,
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
print("Stealth applied")

page = ctx.pages[0] if ctx.pages else ctx.new_page()

# CDP blocked URLs (same as production)
cdp = ctx.new_cdp_session(page)
cdp.send("Network.enable", {})
blocked = [
    "*mc.yandex.ru*", "*metrika.yandex.ru*", "*google-analytics.com*",
    "*fonts.googleapis.com*", "*fonts.gstatic.com*",
    "*.woff2*", "*.woff*", "*.ttf*", "*.otf*",
]
cdp.send("Network.setBlockedURLs", {"urls": blocked})
print(f"CDP blocked {len(blocked)} URL patterns")

# Apply _ANALYTICS_KILL_JS (abbreviated version)
kill_js = """
(function() {
    if (navigator.sendBeacon) {
        navigator.sendBeacon = function() { return true; };
    }
    window.Ya = window.Ya || {};
    window.Ya.Metrika2 = function() {
        return { reachGoal: function(){}, hit: function(){} };
    };
    window.Ya.Metrika = window.Ya.Metrika2;
})();
"""
cdp.send("Page.addScriptToEvaluateOnNewDocument", {"source": kill_js})
print("Analytics kill JS injected")

# Navigate to ya.ru
print("Navigating to ya.ru...")
page.goto("https://ya.ru", timeout=30000)
time.sleep(3)

title = page.title()
url = page.url
print(f"TITLE: '{title}'")
print(f"URL: {url}")

if not title.strip():
    print("REPRODUCED: Empty title!")
    # Try to get page content
    html = page.content()
    print(f"HTML length: {len(html)}")
    print(f"HTML first 500 chars: {html[:500]}")
else:
    print("OK: Title is present, issue NOT reproduced")

ctx.close()
p.stop()
