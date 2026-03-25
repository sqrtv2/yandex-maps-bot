#!/usr/bin/env python3
"""
Visual test: watch what happens when Yandex SmartCaptcha PoW triggers.
Opens a VISIBLE browser (headless=False) so you can see the page.

Usage:
    python test_captcha_visual.py
    python test_captcha_visual.py --proxy  (use proxy like production)
"""
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
import time, sys, os, shutil

USE_PROXY = '--proxy' in sys.argv

# Use a temp profile so we don't pollute real profiles
PROFILE_DIR = '/tmp/test_captcha_visual_profile'
if os.path.exists(PROFILE_DIR):
    shutil.rmtree(PROFILE_DIR)
os.makedirs(PROFILE_DIR, exist_ok=True)

# Same proxy as production tests
PROXY_CONFIG = None
if USE_PROXY:
    PROXY_CONFIG = {
        "server": "http://msk-2.lte-center.ru:4087",
        "username": "sqrtv2",
        "password": "21607141",
    }
    print(f"🌐 Using proxy: {PROXY_CONFIG['server']}")
else:
    print("🌐 No proxy (direct connection)")

# Same Chrome args as production (from browser_manager.py _build_launch_args)
LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-hang-monitor",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--disable-component-extensions-with-background-pages",
    "--js-flags=--max-old-space-size=4096",
    "--disable-features=TranslateUI,BlinkGenPropertyTrees",
    "--disable-ipc-flooding-protection",
    "--enforce-webrtc-ip-permission-check",
    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--lang=ru-RU",
]

print("=" * 70)
print("VISUAL CAPTCHA TEST — watch the browser window")
print("=" * 70)

p = sync_playwright().start()

ctx = p.chromium.launch_persistent_context(
    user_data_dir=PROFILE_DIR,
    headless=False,  # VISIBLE!
    args=LAUNCH_ARGS,
    proxy=PROXY_CONFIG,
    viewport={"width": 1366, "height": 768},
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
print("✅ Browser launched with stealth (headless=False)")

page = ctx.pages[0] if ctx.pages else ctx.new_page()

# Listen for page crash events
page.on("crash", lambda: print("💥💥💥 PAGE CRASH EVENT FIRED! 💥💥💥"))
ctx.on("close", lambda _: print("💥 CONTEXT CLOSE EVENT"))

# Monitor Chromium process
browser_pid = None
try:
    # Playwright internal: get browser process PID
    if hasattr(ctx, '_impl_obj') and hasattr(ctx._impl_obj, '_browser'):
        proc = ctx._impl_obj._browser._connection._transport._proc
        browser_pid = proc.pid
        print(f"📊 Chromium main process PID: {browser_pid}")
except Exception as e:
    print(f"⚠️ Could not get browser PID: {e}")


def check_alive(label: str) -> bool:
    """Check if page is still alive."""
    try:
        page.evaluate("1")
        return True
    except Exception as e:
        err_str = str(e)
        if 'navigation' in err_str.lower() or 'context was destroyed' in err_str.lower():
            # Navigation happened — page is alive, just changed context
            print(f"🔄 [{label}] Navigation detected: {type(e).__name__}: {err_str[:150]}")
            time.sleep(1)
            try:
                page.evaluate("1")
                return True
            except Exception as e2:
                print(f"💀 [{label}] Page DEAD after navigation retry: {type(e2).__name__}: {str(e2)[:150]}")
                return False
        print(f"💀 [{label}] Page DEAD: {type(e).__name__}: {str(e)[:150]}")
        return False


def print_status(label: str):
    """Print current page status."""
    try:
        url = page.url
        title = page.title()
        print(f"📋 [{label}] URL: {url[:120]}")
        print(f"📋 [{label}] Title: '{title}'")
    except Exception as e:
        print(f"💀 [{label}] Cannot read status: {e}")


# ── Step 1: Navigate to ya.ru ──
print("\n" + "=" * 50)
print("STEP 1: Opening ya.ru...")
try:
    page.goto("https://ya.ru", timeout=30000)
    time.sleep(3)
    print_status("ya.ru")
except Exception as e:
    print(f"❌ ya.ru navigation failed: {e}")

if not check_alive("after ya.ru"):
    print("Browser died on ya.ru, exiting")
    sys.exit(1)

# ── Step 2: Check for captcha on ya.ru ──
print("\n" + "=" * 50)
print("STEP 2: Checking for captcha...")
try:
    url_lower = page.url.lower()
    source_lower = page.content()[:3000].lower()
    has_captcha = (
        'showcaptcha' in url_lower 
        or 'captchafast' in url_lower
        or 'smartcaptcha' in source_lower 
        or 'checkboxcaptcha' in source_lower
    )
    if has_captcha:
        print("🚨 CAPTCHA DETECTED on ya.ru!")
        print(f"   URL: {page.url}")
        print("   >>> Watch the browser window! Monitoring for 60 seconds...")
        for i in range(60):
            time.sleep(1)
            alive = check_alive(f"captcha {i+1}s")
            if not alive:
                print(f"\n💀 BROWSER DIED AT {i+1} SECONDS DURING CAPTCHA!")
                # Try to check Chrome process
                if browser_pid:
                    import subprocess
                    result = subprocess.run(['ps', '-p', str(browser_pid)], 
                                          capture_output=True, text=True)
                    print(f"   Main process alive: {'yes' if result.returncode == 0 else 'NO'}")
                break
            if (i + 1) % 5 == 0:
                try:
                    print(f"   [{i+1}s] URL: {page.url[:100]}, Title: '{page.title()}'")
                    new_url = page.url.lower()
                    new_path = new_url.split('?')[0]
                    if 'showcaptcha' not in new_path and 'captchafast' not in new_path:
                        print(f"   🎉 Redirected away from captcha at {i+1}s!")
                        break
                except Exception as e:
                    print(f"   [{i+1}s] Error reading status: {e}")
    else:
        print("✅ No captcha on ya.ru")
except Exception as e:
    print(f"❌ Error checking captcha: {e}")

if not check_alive("before search"):
    print("Browser died before search, exiting")
    sys.exit(1)

# ── Step 3: Do a search ──
print("\n" + "=" * 50)
print("STEP 3: Typing search query...")
try:
    # Type into search box
    search_box = page.wait_for_selector('textarea[name="text"], input[name="text"]', timeout=10000)
    if search_box:
        search_box.fill("ремонт квартир москва")
        time.sleep(1)
        print("✅ Typed search query")
        
        # Submit
        page.keyboard.press("Enter")
        print("✅ Pressed Enter, waiting for results...")
        time.sleep(5)
        print_status("after search")
    else:
        print("⚠️ Search box not found, trying direct URL...")
        page.goto("https://ya.ru/search/?text=ремонт+квартир+москва", timeout=30000)
        time.sleep(5)
        print_status("direct search")
except Exception as e:
    print(f"❌ Search failed: {e}")

# ── Step 4: Check for captcha on search results ──
print("\n" + "=" * 50)
print("STEP 4: Checking search results for captcha...")
try:
    url_lower = page.url.lower()
    url_path = url_lower.split('?')[0]
    has_search_captcha = 'showcaptcha' in url_path or 'captchafast' in url_path
    
    if has_search_captcha:
        print(f"🚨 CAPTCHA on search! URL: {page.url[:120]}")
        print("   >>> Watch the browser! Monitoring for 120 seconds...")
        print("   Testing EXACTLY what production code does:")
        print("   - page.url (sync property, no JS eval)")
        print("   - page.title() (JS eval)")
        print("   - page.evaluate('1') (JS eval)")
        
        captcha_resolved = False
        for i in range(120):
            time.sleep(1)
            
            # Test 1: page.url (same as driver.current_url in production)
            try:
                cur_url = page.url
                url_ok = True
            except Exception as e:
                print(f"   [{i+1}s] ❌ page.url FAILED: {type(e).__name__}: {str(e)[:120]}")
                url_ok = False
                cur_url = "UNKNOWN"
            
            # Test 2: page.title() (same as driver.title in production)
            try:
                cur_title = page.title()
                title_ok = True
            except Exception as e:
                err = str(e)
                is_nav = 'navigation' in err.lower() or 'destroyed' in err.lower()
                print(f"   [{i+1}s] {'🔄' if is_nav else '❌'} page.title() FAILED: {type(e).__name__}: {err[:120]}")
                title_ok = False
                cur_title = "ERROR"
            
            # Test 3: page.evaluate('1') (JS eval)
            try:
                page.evaluate("1")
                eval_ok = True
            except Exception as e:
                err = str(e)
                is_nav = 'navigation' in err.lower() or 'destroyed' in err.lower()
                print(f"   [{i+1}s] {'🔄' if is_nav else '❌'} page.evaluate FAILED: {type(e).__name__}: {err[:120]}")
                eval_ok = False
            
            # Summary every second during captcha
            status = f"url={'OK' if url_ok else 'FAIL'} title={'OK' if title_ok else 'FAIL'} eval={'OK' if eval_ok else 'FAIL'}"
            
            if url_ok:
                cur_path = cur_url.lower().split('?')[0]
                if 'showcaptcha' not in cur_path and 'captchafast' not in cur_path and 'checkcaptcha' not in cur_path:
                    print(f"   [{i+1}s] 🎉 Captcha resolved! {status}")
                    print(f"          URL: {cur_url[:120]}")
                    if title_ok:
                        print(f"          Title: '{cur_title}'")
                    captcha_resolved = True
                    break
            
            # Print status every 3 seconds
            if (i + 1) % 3 == 0:
                print(f"   [{i+1}s] {status} | URL: {cur_url[:80]}")
            
            # If all three fail with non-navigation error — truly dead
            if not url_ok and not title_ok and not eval_ok:
                print(f"\n💥 ALL THREE CHECKS FAILED AT {i+1}s — browser truly dead!")
                break
        
        if not captcha_resolved:
            print("   ⏰ Captcha monitoring ended without resolution")
    else:
        print(f"✅ No captcha on search results")
        print(f"   URL: {page.url[:120]}")
        # Count search results
        try:
            results = page.query_selector_all('[data-cid]')
            print(f"   Search results (data-cid): {len(results)}")
        except Exception:
            pass
except Exception as e:
    print(f"❌ Error: {e}")

# ── Step 5: Keep browser open for observation ──
print("\n" + "=" * 50)
print("STEP 5: Browser stays open. Press Ctrl+C to close.")
print("   You can interact with the browser window manually.")
try:
    while True:
        time.sleep(5)
        if not check_alive("idle"):
            print("Browser died while idle!")
            break
except KeyboardInterrupt:
    print("\n👋 Closing browser...")

try:
    ctx.close()
    p.stop()
except Exception:
    pass
print("Done.")
