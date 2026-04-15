"""Test launch_persistent_context - reproduce the TargetClosedError."""
import os, sys, time, glob

# Clean singletons
profile_dir = "/app/browser_profiles/Profile-26836"
for f in ["SingletonLock", "SingletonSocket", "SingletonCookie"]:
    p = os.path.join(profile_dir, f)
    if os.path.exists(p) or os.path.islink(p):
        os.remove(p)

os.environ["DISPLAY"] = ":99"

from playwright.sync_api import sync_playwright

chromium_paths = sorted(glob.glob('/opt/pw-browsers/chromium-*/chrome-linux*/chrome'))
chromium_exe = chromium_paths[-1] if chromium_paths else None
print("Chrome binary:", chromium_exe)

pw = sync_playwright().start()
print("Playwright started, node driver PID:", os.getpid())

args = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-hang-monitor",
    "--js-flags=--max-old-space-size=1024",
    "--disable-ipc-flooding-protection",
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    "--disable-renderer-backgrounding",
    "--disable-features=TranslateUI,BlinkGenPropertyTrees",
    "--lang=ru-RU",
]

# Test 1: First launch
print("\n=== TEST 1: First launch ===")
t0 = time.time()
try:
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=profile_dir,
        headless=False,
        executable_path=chromium_exe,
        args=args,
        viewport={"width": 1366, "height": 768},
        ignore_https_errors=True,
        timeout=15000,
    )
    dt = time.time() - t0
    print(f"SUCCESS in {dt:.2f}s, pages: {len(ctx.pages)}")
    ctx.close()
    print("Context closed")
except Exception as e:
    dt = time.time() - t0
    # Only print first 200 chars of error
    err_str = str(e)
    if len(err_str) > 300:
        err_str = err_str[:150] + "\n...\n" + err_str[-150:]
    print(f"FAILED in {dt:.2f}s: {err_str}")

# Clean again
for f in ["SingletonLock", "SingletonSocket", "SingletonCookie"]:
    p = os.path.join(profile_dir, f)
    if os.path.exists(p) or os.path.islink(p):
        os.remove(p)

time.sleep(1)

# Test 2: Second launch (same Playwright instance)
print("\n=== TEST 2: Second launch (same pw instance) ===")
t0 = time.time()
try:
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=profile_dir,
        headless=False,
        executable_path=chromium_exe,
        args=args,
        viewport={"width": 1366, "height": 768},
        ignore_https_errors=True,
        timeout=15000,
    )
    dt = time.time() - t0
    print(f"SUCCESS in {dt:.2f}s, pages: {len(ctx.pages)}")
    ctx.close()
    print("Context closed")
except Exception as e:
    dt = time.time() - t0
    err_str = str(e)
    if len(err_str) > 300:
        err_str = err_str[:150] + "\n...\n" + err_str[-150:]
    print(f"FAILED in {dt:.2f}s: {err_str}")

# Clean again
for f in ["SingletonLock", "SingletonSocket", "SingletonCookie"]:
    p = os.path.join(profile_dir, f)
    if os.path.exists(p) or os.path.islink(p):
        os.remove(p)

time.sleep(1)

# Test 3: Third launch (stress test)
print("\n=== TEST 3: Third launch ===")
t0 = time.time()
try:
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=profile_dir,
        headless=False,
        executable_path=chromium_exe,
        args=args,
        viewport={"width": 1366, "height": 768},
        ignore_https_errors=True,
        timeout=15000,
    )
    dt = time.time() - t0
    print(f"SUCCESS in {dt:.2f}s, pages: {len(ctx.pages)}")
    ctx.close()
    print("Context closed")
except Exception as e:
    dt = time.time() - t0
    err_str = str(e)
    if len(err_str) > 300:
        err_str = err_str[:150] + "\n...\n" + err_str[-150:]
    print(f"FAILED in {dt:.2f}s: {err_str}")

pw.stop()
print("\nDone. All tests completed.")
