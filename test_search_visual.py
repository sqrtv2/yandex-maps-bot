"""
Simple visual Yandex search test — ONE browser at a time, no threads.
Uses Playwright directly for reliability.

Usage: python test_search_visual.py
"""
import time
import random
from urllib.parse import quote_plus, urlparse
from playwright.sync_api import sync_playwright

# === CONFIG ===
TESTS = [
    {"domain": "povoenke.ru", "keyword": "военная ипотека 2026"},
    {"domain": "povoenke.ru", "keyword": "втб ипотека военная"},
    {"domain": "ecoinstrument.ru", "keyword": "ph метр"},
]

PROXIES = [
    {"server": "http://95.31.178.33:4055", "username": "sqrtv2", "password": "21607141"},
    {"server": "http://95.31.178.33:4054", "username": "sqrtv2", "password": "21607141"},
    {"server": "http://95.31.170.9:4097",  "username": "sqrtv2", "password": "21607141"},
]

MAX_PAGES = 5
PROFILE_DIR = "./browser_profiles/Test-Visual"


def run_test(test_config, proxy, test_num):
    domain = test_config["domain"]
    keyword = test_config["keyword"]
    print(f"\n{'='*60}")
    print(f"TEST #{test_num}: '{keyword}' → {domain}")
    print(f"Proxy: {proxy['server']}")
    print(f"{'='*60}")

    with sync_playwright() as p:
        profile_dir = f"{PROFILE_DIR}-{test_num}"
        context = p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--no-default-browser-check",
                "--lang=ru-RU",
                "--disable-features=TranslateUI",
            ],
            proxy=proxy,
            viewport={"width": 1366, "height": 768},
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            ignore_https_errors=True,
        )

        page = context.pages[0] if context.pages else context.new_page()

        # Step 1: Open ya.ru
        print(f"\n[{test_num}] Opening ya.ru...")
        try:
            page.goto("https://ya.ru", timeout=30000)
        except Exception as e:
            print(f"[{test_num}] ya.ru load timeout: {e}")

        time.sleep(2)
        url = page.url
        title = page.title()
        print(f"[{test_num}] URL: {url}")
        print(f"[{test_num}] Title: {title}")

        # Check captcha
        if "showcaptcha" in url.lower() or "captcha" in url.lower():
            print(f"[{test_num}] ⚠️ CAPTCHA detected! URL: {url}")
            input(f"[{test_num}] Solve captcha manually, then press Enter...")

        # Step 2: Search
        search_url = f"https://ya.ru/search/?text={quote_plus(keyword)}"
        print(f"\n[{test_num}] Searching: {keyword}")
        try:
            page.goto(search_url, timeout=30000)
        except Exception as e:
            print(f"[{test_num}] Search timeout: {e}")

        time.sleep(3)
        current_url = page.url
        print(f"[{test_num}] Search URL: {current_url[:120]}")

        if "showcaptcha" in current_url.lower():
            print(f"[{test_num}] ⚠️ CAPTCHA on search!")
            input(f"[{test_num}] Solve captcha manually, then press Enter...")
            # Reload after captcha
            try:
                page.goto(search_url, timeout=30000)
            except Exception:
                pass
            time.sleep(3)

        # Step 3: Scan pages
        found_on_page = None
        for page_num in range(MAX_PAGES):
            print(f"\n[{test_num}] --- Page {page_num + 1} ---")

            # Take screenshot
            try:
                ss_path = f"screenshots/test_visual_{test_num}_page{page_num+1}.png"
                page.screenshot(path=ss_path)
                print(f"[{test_num}] Screenshot: {ss_path}")
            except Exception:
                pass

            # Get all links
            links = page.query_selector_all("a[href]")
            results = []
            for link in links:
                try:
                    href = link.get_attribute("href") or ""
                    text = (link.inner_text() or "").strip()[:80]
                    if not href or not text or len(text) < 10:
                        continue
                    if not href.startswith("http"):
                        continue
                    parsed = urlparse(href)
                    link_domain = parsed.netloc.replace("www.", "")
                    if any(skip in link_domain for skip in ["yandex", "ya.ru", "yastatic"]):
                        continue
                    if link_domain:
                        results.append((link_domain, text[:60], href[:100]))
                except Exception:
                    continue

            # Print results
            for idx, (dom, txt, href) in enumerate(results[:20], 1):
                marker = " ✅ FOUND!" if domain in dom else ""
                print(f"[{test_num}]   #{idx}: {dom} — {txt}{marker}")

            if not results:
                print(f"[{test_num}]   (no results found on this page)")

            # Check match
            if any(domain in r[0] for r in results):
                found_on_page = page_num + 1
                print(f"\n[{test_num}] 🎉 DOMAIN '{domain}' FOUND on page {found_on_page}!")
                break
            else:
                print(f"[{test_num}] ❌ '{domain}' NOT on page {page_num + 1}")

            # Next page
            if page_num < MAX_PAGES - 1:
                next_url = current_url
                parsed_url = urlparse(next_url)
                from urllib.parse import parse_qs, urlencode
                params = parse_qs(parsed_url.query)
                params['p'] = [str(page_num + 1)]
                new_query = urlencode({k: v[0] for k, v in params.items()})
                next_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}?{new_query}"
                print(f"[{test_num}] → Going to page {page_num + 2}...")
                try:
                    page.goto(next_url, timeout=30000)
                except Exception:
                    pass
                time.sleep(random.uniform(2, 4))

        # Summary
        print(f"\n[{test_num}] === RESULT: ", end="")
        if found_on_page:
            print(f"'{domain}' found on page {found_on_page} for '{keyword}'")
        else:
            print(f"'{domain}' NOT found in {MAX_PAGES} pages for '{keyword}'")

        print(f"[{test_num}] Browser staying open 15 seconds for inspection...")
        time.sleep(15)
        context.close()


def main():
    import os
    os.makedirs("screenshots", exist_ok=True)

    print("🔍 Visual Yandex Search Test")
    print(f"Tests: {len(TESTS)}")
    for i, t in enumerate(TESTS):
        print(f"  {i+1}. '{t['keyword']}' → {t['domain']}")
    print()

    for i, test in enumerate(TESTS):
        proxy = PROXIES[i % len(PROXIES)]
        try:
            run_test(test, proxy, i + 1)
        except Exception as e:
            print(f"\n❌ Test {i+1} failed: {e}")
            import traceback
            traceback.print_exc()

    print("\n\n🏁 ALL TESTS COMPLETE")


if __name__ == "__main__":
    main()
