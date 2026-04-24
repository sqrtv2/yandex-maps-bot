"""Multi-URL test through proxy to find what specifically dies."""
import time, sys, glob
from rebrowser_playwright.sync_api import sync_playwright

PROXY = {"server": "http://95.31.170.9:4102", "username": "sqrtv2", "password": "21607141"}
chrome = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome"))[-1]

def log(m): print(time.strftime("%H:%M:%S"), m, flush=True)

log(f"chrome={chrome}")
pw = sync_playwright().start()
log("pw started")
ctx = pw.chromium.launch_persistent_context(
    user_data_dir="/tmp/t2_prof",
    headless=False, executable_path=chrome,
    args=["--no-sandbox", "--disable-dev-shm-usage"],
    proxy=PROXY,
    viewport={"width": 1366, "height": 768}, locale="ru-RU",
    ignore_https_errors=True, timeout=60000,
)
log("ctx ok")
page = ctx.pages[0] if ctx.pages else ctx.new_page()

# capture console + network errors
page.on("console", lambda msg: log(f"  console[{msg.type}]: {msg.text[:200]}"))
page.on("pageerror", lambda err: log(f"  PAGEERROR: {str(err)[:200]}"))
page.on("requestfailed", lambda req: log(f"  REQFAIL: {req.method} {req.url[:80]} → {req.failure}"))
page.on("response", lambda resp: log(f"  resp: {resp.status} {resp.url[:80]}") if resp.status >= 400 else None)
page.on("crash", lambda p: log(f"  💀 PAGE CRASHED"))
page.on("close", lambda p: log(f"  ❌ page closed"))

for url in ["https://example.com/", "https://httpbin.org/ip", "https://ya.ru/", "https://yandex.ru/"]:
    log(f"=== goto {url}")
    t0 = time.monotonic()
    try:
        r = page.goto(url, timeout=30000, wait_until="commit")
        log(f"  OK {time.monotonic()-t0:.1f}s status={r.status if r else None} url={page.url[:100]}")
        time.sleep(2)
        try:
            t = page.title()
            log(f"  title={t[:80]!r}")
        except Exception as e:
            log(f"  title err: {type(e).__name__}: {str(e)[:120]}")
            break
    except Exception as e:
        log(f"  ERR {time.monotonic()-t0:.1f}s {type(e).__name__}: {str(e)[:200]}")
        # don't break — try next
    time.sleep(2)

log("done")
try: ctx.close()
except: pass
pw.stop()
