"""Test all proxies against Yandex search to check captcha status"""
from playwright.sync_api import sync_playwright
import time

PROXIES = [
    ("95.31.170.9", 4097),
    ("95.31.170.9", 4102),
    ("95.31.178.33", 4054),
    ("95.31.178.33", 4055),
    ("msk-2.lte-center.ru", 4087),
]

SEARCH_URL = "https://ya.ru/search/?text=%D0%B2%D0%BE%D0%B5%D0%BD%D0%BD%D0%B0%D1%8F+%D0%B8%D0%BF%D0%BE%D1%82%D0%B5%D0%BA%D0%B0+%D0%B2%D1%82%D0%B1&lr=213"

with sync_playwright() as p:
    for host, port in PROXIES:
        print(f"=== {host}:{port} ===", flush=True)
        try:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            ctx = browser.new_context(
                proxy={"server": f"http://{host}:{port}", "username": "sqrtv2", "password": "21607141"},
                locale="ru-RU",
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = ctx.new_page()
            page.goto(SEARCH_URL, timeout=40000)
            time.sleep(2)
            
            url = page.url
            title = page.title()
            captcha = "showcaptcha" in url.lower() or "robot" in title.lower() or "captcha" in url.lower()
            
            if captcha:
                print(f"  ❌ CAPTCHA! title='{title[:50]}' url={url[:80]}", flush=True)
            else:
                # Count organic results
                results = page.query_selector_all("[data-cid]")
                # Check for povoenke.ru in page source
                src = page.content()
                has_povoenke = "povoenke.ru" in src
                print(f"  ✅ OK title='{title[:50]}' results={len(results)} povoenke_in_page={has_povoenke}", flush=True)
            
            browser.close()
        except Exception as e:
            print(f"  ⚠️ ERROR: {str(e)[:100]}", flush=True)
            try:
                browser.close()
            except:
                pass
