"""Test with full Chrome stderr + DEBUG logs to find what kills Chrome on first HTTPS request."""
import time, sys, os, glob, subprocess

os.environ["DEBUG"] = "pw:browser*,pw:protocol*"
os.environ["PWDEBUG"] = "1"  # verbose

PROXY = {"server": "http://95.31.170.9:4102", "username": "sqrtv2", "password": "21607141"}
chrome = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome"))[-1]

def log(m): print(time.strftime("%H:%M:%S"), m, flush=True)

log(f"chrome={chrome}")

# Manual launch to capture stderr
from rebrowser_playwright.sync_api import sync_playwright

pw = sync_playwright().start()
log("pw started")
ctx = pw.chromium.launch_persistent_context(
    user_data_dir="/tmp/t3_prof",
    headless=False, executable_path=chrome,
    args=[
        "--no-sandbox", "--disable-dev-shm-usage",
        "--enable-logging=stderr", "--v=1",
        "--log-net-log=/tmp/t3_netlog.json",
    ],
    proxy=PROXY,
    viewport={"width": 1366, "height": 768}, locale="ru-RU",
    ignore_https_errors=True, timeout=60000,
)
log("ctx ok")
page = ctx.pages[0] if ctx.pages else ctx.new_page()

page.on("requestfailed", lambda req: log(f"  REQFAIL: {req.method} {req.url[:80]} → {req.failure}"))
page.on("crash", lambda p: log(f"  💀 PAGE CRASHED"))

log("=== goto https://example.com/")
t0 = time.monotonic()
try:
    r = page.goto("https://example.com/", timeout=30000, wait_until="commit")
    log(f"  OK {time.monotonic()-t0:.1f}s status={r.status if r else None}")
except Exception as e:
    log(f"  ERR {time.monotonic()-t0:.1f}s {type(e).__name__}: {str(e)[:300]}")

# After crash — read net log
log("--- chrome net log tail (last 50 lines, grep CONNECT/SSL/PROXY)")
try:
    with open("/tmp/t3_netlog.json", "r") as f:
        data = f.read()
    log(f"netlog size: {len(data)} bytes")
    # netlog is JSON, but partial when crashed — find error events
    import re
    for m in re.finditer(r'"type":\s*\d+[^}]{0,200}(?:CONNECT|SSL|PROXY|ERR_|HTTP2_SESSION)[^}]{0,200}', data):
        log(f"  {m.group()[:250]}")
except Exception as e:
    log(f"netlog read err: {e}")

try: ctx.close()
except: pass
pw.stop()
