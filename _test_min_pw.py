"""Minimal: just launch Playwright + goto ya.ru with hard timeout to see what hangs."""
import sys, time, threading
from rebrowser_playwright.sync_api import sync_playwright

PROXY = {"server": "http://95.31.170.9:4102", "username": "sqrtv2", "password": "21607141"}

def log(m): print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)

def hard_timer(seconds, label):
    def boom():
        log(f"⏰ HARD TIMEOUT {seconds}s — {label} still running")
    t = threading.Timer(seconds, boom)
    t.daemon = True; t.start()
    return t

import glob
chrome = sorted(glob.glob('/opt/pw-browsers/chromium-*/chrome-linux*/chrome'))[-1]
log(f"chrome={chrome}")

pw = sync_playwright().start()
log("playwright started")

t = hard_timer(60, "launch")
ctx = pw.chromium.launch_persistent_context(
    user_data_dir="/tmp/test_min_profile",
    headless=False,
    executable_path=chrome,
    args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
    proxy=PROXY,
    viewport={"width": 1366, "height": 768},
    locale="ru-RU",
    ignore_https_errors=True,
    timeout=60000,
)
t.cancel(); log("✅ context launched")

page = ctx.pages[0] if ctx.pages else ctx.new_page()
log(f"page ready, url={page.url}")

# attempt 1: wait_until=commit (как в проде)
log("→ goto(ya.ru) wait_until=commit timeout=30s")
t = hard_timer(45, "goto commit")
t0 = time.monotonic()
try:
    resp = page.goto("https://ya.ru/", timeout=30000, wait_until="commit")
    log(f"✅ goto commit done in {time.monotonic()-t0:.1f}s, status={resp.status if resp else None}, url={page.url}")
except Exception as e:
    log(f"❌ goto commit raised in {time.monotonic()-t0:.1f}s: {type(e).__name__}: {str(e)[:200]}")
t.cancel()

log(f"current url={page.url}, title={page.title()[:80]!r}")

# attempt 2: wait_until=domcontentloaded
log("→ goto(ya.ru) wait_until=domcontentloaded timeout=30s")
t = hard_timer(45, "goto dom")
t0 = time.monotonic()
try:
    resp = page.goto("https://ya.ru/", timeout=30000, wait_until="domcontentloaded")
    log(f"✅ goto dom done in {time.monotonic()-t0:.1f}s, status={resp.status if resp else None}, url={page.url}")
except Exception as e:
    log(f"❌ goto dom raised in {time.monotonic()-t0:.1f}s: {type(e).__name__}: {str(e)[:200]}")
t.cancel()

log(f"current url={page.url}, title={page.title()[:80]!r}")
log("done, closing")
ctx.close(); pw.stop()
