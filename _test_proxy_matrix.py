"""Test 5: Playwright + proxy with NO auth flag, then with auth, see difference.
Also captures Chrome stderr and Playwright protocol messages."""
import time, glob, os, sys, threading
from rebrowser_playwright.sync_api import sync_playwright

# capture playwright protocol
os.environ["DEBUG"] = "pw:protocol"

chrome = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome"))[-1]

def log(m): print(time.strftime("%H:%M:%S"), m, flush=True)

def run(label, proxy_dict):
    log(f"========== {label} ==========")
    log(f"proxy={proxy_dict}")
    pw = sync_playwright().start()
    try:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=f"/tmp/t5_{label.replace(' ','_')}",
            headless=False, executable_path=chrome,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
            proxy=proxy_dict,
            viewport={"width": 1366, "height": 768}, locale="ru-RU",
            ignore_https_errors=True, timeout=60000,
        )
        log("ctx ok")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on("requestfailed", lambda req: log(f"  REQFAIL: {req.method} {req.url[:90]} → {req.failure}"))
        log("→ goto example.com")
        t0 = time.monotonic()
        try:
            r = page.goto("https://example.com/", timeout=20000, wait_until="commit")
            log(f"  ✅ {time.monotonic()-t0:.1f}s status={r.status if r else None}")
            time.sleep(1)
            log(f"  title={page.title()[:60]!r}")
        except Exception as e:
            log(f"  ❌ {time.monotonic()-t0:.1f}s {type(e).__name__}: {str(e)[:200]}")
        try: ctx.close()
        except: pass
    finally:
        try: pw.stop()
        except: pass
    time.sleep(2)

# Test A: NO proxy
run("A_no_proxy", None)
# Test B: proxy WITHOUT auth
run("B_proxy_no_auth", {"server": "http://95.31.170.9:4102"})
# Test C: proxy WITH auth
run("C_proxy_with_auth", {"server": "http://95.31.170.9:4102", "username": "sqrtv2", "password": "21607141"})
# Test D: same proxy, second time (in case rotation matters)
run("D_proxy_with_auth_2", {"server": "http://95.31.170.9:4102", "username": "sqrtv2", "password": "21607141"})
# Test E: different proxy port
run("E_other_port", {"server": "http://95.31.178.33:4055", "username": "sqrtv2", "password": "21607141"})

log("ALL DONE")
