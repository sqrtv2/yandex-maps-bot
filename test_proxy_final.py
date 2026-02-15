#!/usr/bin/env python3
"""Quick test: verify that the new local proxy forwarder works in BrowserManager."""
import os, sys, time
os.environ['YANDEX_BOT_BROWSER_HEADLESS'] = 'false'
sys.path.insert(0, os.path.dirname(__file__))

from core.browser_manager import BrowserManager

proxy_data = {
    'id': 1,
    'host': 'mproxy.site',
    'port': 12138,
    'username': 'Hes9yF',
    'password': 'zAU2vaEUf4TU',
    'proxy_type': 'http',
}

profile_data = {
    'name': 'Profile-1',
    'language': 'ru-RU',
    'timezone': 'Europe/Moscow',
    'viewport': {'width': 1366, 'height': 768},
    'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
    'hardware_concurrency': 4,
    'device_memory': 8,
    'platform': 'Win32',
    'webgl_fingerprint': {'vendor': 'Google Inc.', 'renderer': 'ANGLE'},
    'chrome_flags': [],
}

bm = BrowserManager()
print("Creating browser session with proxy...")
browser_id = bm.create_browser_session(profile_data, proxy_data)
print(f"✅ Browser created: {browser_id}")

driver = bm.active_browsers[browser_id]

# Navigate to yandex.ru/internet to check IP
print("\nNavigating to yandex.ru/internet to check IP...")
driver.get("https://yandex.ru/internet/")
time.sleep(6)

body = driver.find_element("tag name", "body").text
print(f"\n📄 Page content:\n{body[:500]}")
print(f"\n📍 URL: {driver.current_url}")

# Check if we see Moscow
if "Москва" in body or "москва" in body.lower():
    print("\n✅✅✅ PROXY WORKS! IP is in Moscow!")
elif "Франкфурт" in body:
    print("\n❌❌❌ PROXY NOT WORKING! Still Frankfurt!") 
else:
    print("\n⚠️ Unknown location — check the output above")

# Now test yandex.ru/maps
print("\n\nNavigating to Benesque on Yandex Maps...")
driver.get("https://yandex.ru/maps/org/benesque/193289471730/")
time.sleep(8)

print(f"📍 URL: {driver.current_url}")
print(f"📄 Title: {driver.title}")

if "yandex.ru" in driver.current_url:
    print("✅ Stayed on yandex.RU — proxy is Russian!")
elif "yandex.com" in driver.current_url:
    print("⚠️ Redirected to yandex.COM — check proxy geo")

input("\n⏎ Press Enter to close browser...")
bm.close_browser_session(browser_id)
print("Done!")
