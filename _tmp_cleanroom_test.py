"""Clean-room Chrome launch test via Playwright."""
import subprocess, os, time, sys, glob

chrome_bin = "/opt/pw-browsers/chromium-1169/chrome-linux/chrome"

print("=== Step 1: Kill ALL Chrome processes ===")
subprocess.run(["pkill", "-9", "-f", "chrome.*--no-sandbox"], capture_output=True, timeout=5)
time.sleep(1)
result = subprocess.run(["pgrep", "-c", "chrome"], capture_output=True, text=True)
chrome_count = result.stdout.strip()
print(f"Chrome processes remaining: {chrome_count}")

print("\n=== Step 2: Kill ALL node drivers ===")
subprocess.run(["pkill", "-9", "-f", "run-driver"], capture_output=True, timeout=5)
time.sleep(1)

print("\n=== Step 3: Pick a test profile ===")
profile = "/app/browser_profiles/Profile-26836"
for f in ["SingletonLock", "SingletonSocket", "SingletonCookie"]:
    p = os.path.join(profile, f)
    if os.path.exists(p) or os.path.islink(p):
        os.remove(p)

os.environ["DISPLAY"] = ":99"

print("\n=== Step 4: Launch via Playwright (clean room) ===")
from playwright.sync_api import sync_playwright

pw = sync_playwright().start()
print("Playwright started")

args = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-hang-monitor",
    "--js-flags=--max-old-space-size=1024",
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    "--disable-features=TranslateUI,BlinkGenPropertyTrees",
    "--lang=ru-RU",
]

t0 = time.time()
try:
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=profile,
        headless=False,
        executable_path=chrome_bin,
        args=args,
        viewport={"width": 1366, "height": 768},
        ignore_https_errors=True,
        timeout=30000,
    )
    dt = time.time() - t0
    print(f"SUCCESS in {dt:.2f}s!")
    print(f"Pages: {len(ctx.pages)}")
    if ctx.pages:
        print(f"Page URL: {ctx.pages[0].url}")
    ctx.close()
except Exception as e:
    dt = time.time() - t0
    err_str = str(e)
    lines = err_str.split("\n")
    non_dbus = [l for l in lines if "dbus" not in l.lower() and l.strip()]
    print(f"FAILED in {dt:.2f}s")
    print(f"Non-dbus error lines ({len(non_dbus)}):")
    for l in non_dbus[:30]:
        print(f"  {l[:250]}")

pw.stop()
print("\nDone")
