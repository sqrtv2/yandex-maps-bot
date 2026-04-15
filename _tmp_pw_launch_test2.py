"""Extended Chrome launch diagnostic with full error capture."""
import os, sys, time, glob

profile_dir = "/app/browser_profiles/Profile-26836"
chrome_bin = "/opt/pw-browsers/chromium-1169/chrome-linux/chrome"

# Clean singletons
for f in ["SingletonLock", "SingletonSocket", "SingletonCookie"]:
    p = os.path.join(profile_dir, f)
    if os.path.exists(p) or os.path.islink(p):
        os.remove(p)

os.environ["DISPLAY"] = ":99"

from playwright.sync_api import sync_playwright

print("Chrome binary:", chrome_bin)
pw = sync_playwright().start()

# Test with FULL Playwright defaults (no custom args)
print("\n=== TEST A: Minimal args (no swiftshader) ===")
t0 = time.time()
try:
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=profile_dir,
        headless=False,
        executable_path=chrome_bin,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
        viewport={"width": 1366, "height": 768},
        ignore_https_errors=True,
        timeout=15000,
    )
    dt = time.time() - t0
    print(f"SUCCESS in {dt:.2f}s")
    ctx.close()
except Exception as e:
    dt = time.time() - t0
    err_lines = str(e).split("\n")
    non_dbus = [l for l in err_lines if "dbus" not in l.lower() and l.strip()]
    print(f"FAILED in {dt:.2f}s, non-dbus errors:")
    for l in non_dbus[:15]:
        print(f"  {l[:200]}")

# Clean again
for f in ["SingletonLock", "SingletonSocket", "SingletonCookie"]:
    p = os.path.join(profile_dir, f)
    if os.path.exists(p) or os.path.islink(p):
        os.remove(p)
time.sleep(1)

# Test with headless=True
print("\n=== TEST B: headless=True ===")
t0 = time.time()
try:
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=profile_dir,
        headless=True,
        executable_path=chrome_bin,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
        viewport={"width": 1366, "height": 768},
        ignore_https_errors=True,
        timeout=15000,
    )
    dt = time.time() - t0
    print(f"SUCCESS in {dt:.2f}s")
    ctx.close()
except Exception as e:
    dt = time.time() - t0
    err_lines = str(e).split("\n")
    non_dbus = [l for l in err_lines if "dbus" not in l.lower() and l.strip()]
    print(f"FAILED in {dt:.2f}s, non-dbus errors:")
    for l in non_dbus[:15]:
        print(f"  {l[:200]}")

# Clean again
for f in ["SingletonLock", "SingletonSocket", "SingletonCookie"]:
    p = os.path.join(profile_dir, f)
    if os.path.exists(p) or os.path.islink(p):
        os.remove(p)
time.sleep(1)

# Test with a FRESH profile dir
import tempfile
fresh_profile = tempfile.mkdtemp(prefix="test_profile_")
print(f"\n=== TEST C: Fresh profile dir ({fresh_profile}) ===")
t0 = time.time()
try:
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=fresh_profile,
        headless=False,
        executable_path=chrome_bin,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
        viewport={"width": 1366, "height": 768},
        ignore_https_errors=True,
        timeout=15000,
    )
    dt = time.time() - t0
    print(f"SUCCESS in {dt:.2f}s")
    ctx.close()
except Exception as e:
    dt = time.time() - t0
    err_lines = str(e).split("\n")
    non_dbus = [l for l in err_lines if "dbus" not in l.lower() and l.strip()]
    print(f"FAILED in {dt:.2f}s, non-dbus errors:")
    for l in non_dbus[:15]:
        print(f"  {l[:200]}")

# Clean temp
import shutil
shutil.rmtree(fresh_profile, ignore_errors=True)

pw.stop()
print("\nAll tests done")
