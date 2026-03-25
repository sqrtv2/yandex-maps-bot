#!/usr/bin/env python3
"""Compare page.title() vs page.evaluate('document.title')"""
from playwright.sync_api import sync_playwright
import time, os

profile_dir = "./browser_profiles/Profile-Test-Debug3"
os.makedirs(profile_dir, exist_ok=True)
lock = os.path.join(profile_dir, "SingletonLock")
if os.path.exists(lock) or os.path.islink(lock):
    os.remove(lock)

p = sync_playwright().start()
ctx = p.chromium.launch_persistent_context(
    user_data_dir=profile_dir,
    headless=True,
    args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
    proxy={"server": "http://msk-2.lte-center.ru:4087", "username": "sqrtv2", "password": "21607141"},
    viewport={"width": 1366, "height": 768},
    locale="ru-RU",
    timezone_id="Europe/Moscow",
    ignore_https_errors=True,
)

page = ctx.pages[0] if ctx.pages else ctx.new_page()

# Go to ya.ru with timeout like production (40s)
print("=== Navigating to ya.ru ===")
try:
    page.goto("https://ya.ru", timeout=40000)
except Exception as e:
    print(f"  goto timed out: {e}")
    try:
        page.evaluate("window.stop()")
    except:
        pass

# Check title IMMEDIATELY (no sleep - simulates fast check)
print("\n=== IMMEDIATELY after goto ===")
try:
    t1 = page.title()
    print(f"  page.title() = '{t1}'")
except Exception as e:
    print(f"  page.title() error: {e}")

try:
    t2 = page.evaluate("() => document.title")
    print(f"  page.evaluate('document.title') = '{t2}'")
except Exception as e:
    print(f"  page.evaluate() error: {e}")

# Wait 1s
time.sleep(1)
print("\n=== After 1s ===")
try:
    t3 = page.title()
    print(f"  page.title() = '{t3}'")
except Exception as e:
    print(f"  page.title() error: {e}")
try:
    t4 = page.evaluate("() => document.title")
    print(f"  page.evaluate('document.title') = '{t4}'")
except Exception as e:
    print(f"  page.evaluate() error: {e}")

# Wait 3s
time.sleep(3)
print("\n=== After 4s total ===")
try:
    t5 = page.title()
    print(f"  page.title() = '{t5}'")
except Exception as e:
    print(f"  page.title() error: {e}")
try:
    t6 = page.evaluate("() => document.title")
    print(f"  page.evaluate('document.title') = '{t6}'")
except Exception as e:
    print(f"  page.evaluate() error: {e}")

# Now test: goto ya.ru with waitUntil=domcontentloaded vs load
print("\n\n=== Test: goto ya.ru with wait_until=domcontentloaded ===")
page2 = ctx.new_page()
try:
    page2.goto("https://ya.ru", timeout=40000, wait_until="domcontentloaded")
except Exception as e:
    print(f"  goto timed out: {e}")
print(f"  page.title() = '{page2.title()}'")
print(f"  page.evaluate('document.title') = '{page2.evaluate('() => document.title')}'")

ctx.close()
p.stop()
