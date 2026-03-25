"""
Server-side visual debug: run headless Chrome through proxy, 
capture screenshots + HTML at each step to see what the bot sees.

Usage: python3 test_search_server.py
"""
import time
import random
import os
from urllib.parse import quote_plus, urlparse, parse_qs, urlencode
from playwright.sync_api import sync_playwright

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
OUTPUT_DIR = "/tmp/search_debug"


def run_test(test_config, proxy, test_num):
    domain = test_config["domain"]
    keyword = test_config["keyword"]
    test_dir = os.path.join(OUTPUT_DIR, f"test_{test_num}")
    os.makedirs(test_dir, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"TEST #{test_num}: '{keyword}' -> {domain}")
    print(f"Proxy: {proxy['server']}")
    print(f"Output: {test_dir}")
    print(f"{'='*60}")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=f"/tmp/test_profile_{test_num}",
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--no-default-browser-check",
                "--lang=ru-RU",
                "--disable-features=TranslateUI",
                "--disable-gpu",
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
            page.goto("https://ya.ru", timeout=30000, wait_until="domcontentloaded")
        except Exception as e:
            print(f"[{test_num}] ya.ru timeout: {e}")

        time.sleep(2)
        url = page.url
        title = page.title()
        print(f"[{test_num}] URL: {url}")
        print(f"[{test_num}] Title: {title}")

        # Screenshot + HTML
        page.screenshot(path=os.path.join(test_dir, "01_yaru.png"), full_page=True)
        with open(os.path.join(test_dir, "01_yaru.html"), "w") as f:
            f.write(page.content())

        # Check captcha
        if "showcaptcha" in url.lower() or "captcha" in url.lower():
            print(f"[{test_num}] ⚠️  CAPTCHA on ya.ru! URL: {url}")
            page.screenshot(path=os.path.join(test_dir, "01_captcha.png"), full_page=True)
            with open(os.path.join(test_dir, "01_captcha.html"), "w") as f:
                f.write(page.content())

        # Step 2: Search
        search_url = f"https://ya.ru/search/?text={quote_plus(keyword)}"
        print(f"\n[{test_num}] Searching: {keyword}")
        try:
            page.goto(search_url, timeout=30000, wait_until="domcontentloaded")
        except Exception as e:
            print(f"[{test_num}] Search timeout: {e}")

        time.sleep(3)
        current_url = page.url
        print(f"[{test_num}] Search URL: {current_url[:150]}")

        page.screenshot(path=os.path.join(test_dir, "02_search.png"), full_page=True)
        with open(os.path.join(test_dir, "02_search.html"), "w") as f:
            f.write(page.content())

        if "showcaptcha" in current_url.lower():
            print(f"[{test_num}] ⚠️  CAPTCHA on search!")

        # Step 3: Scan pages
        found_on_page = None
        for page_num in range(MAX_PAGES):
            print(f"\n[{test_num}] --- Page {page_num + 1} ---")

            ss_name = f"page_{page_num + 1}.png"
            html_name = f"page_{page_num + 1}.html"
            page.screenshot(path=os.path.join(test_dir, ss_name))
            with open(os.path.join(test_dir, html_name), "w") as f:
                f.write(page.content())

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
                marker = " <<< FOUND!" if domain in dom else ""
                print(f"[{test_num}]   #{idx}: {dom} -- {txt}{marker}")

            if not results:
                print(f"[{test_num}]   (no results found on this page)")
                # Dump page text to understand what's shown
                try:
                    body_text = page.inner_text("body")[:500]
                    print(f"[{test_num}]   Page text: {body_text[:200]}")
                except Exception:
                    pass

            # Check match
            if any(domain in r[0] for r in results):
                found_on_page = page_num + 1
                print(f"\n[{test_num}] DOMAIN '{domain}' FOUND on page {found_on_page}!")
                break
            else:
                print(f"[{test_num}] '{domain}' NOT on page {page_num + 1}")

            # Next page
            if page_num < MAX_PAGES - 1:
                parsed_url = urlparse(current_url)
                params = parse_qs(parsed_url.query)
                params['p'] = [str(page_num + 1)]
                new_query = urlencode({k: v[0] for k, v in params.items()})
                next_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}?{new_query}"
                print(f"[{test_num}] -> Going to page {page_num + 2}...")
                try:
                    page.goto(next_url, timeout=30000, wait_until="domcontentloaded")
                except Exception:
                    pass
                time.sleep(random.uniform(2, 4))

        # Summary
        print(f"\n[{test_num}] === RESULT: ", end="")
        if found_on_page:
            print(f"'{domain}' found on page {found_on_page} for '{keyword}'")
        else:
            print(f"'{domain}' NOT found in {MAX_PAGES} pages for '{keyword}'")

        context.close()


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    # Clean old profiles
    for i in range(1, 4):
        import shutil
        d = f"/tmp/test_profile_{i}"
        if os.path.exists(d):
            shutil.rmtree(d, ignore_errors=True)

    print("=" * 60)
    print("Server-side Yandex Search Debug")
    print(f"Output dir: {OUTPUT_DIR}")
    print("=" * 60)

    for i, test in enumerate(TESTS):
        proxy = PROXIES[i % len(PROXIES)]
        try:
            run_test(test, proxy, i + 1)
        except Exception as e:
            print(f"\nTest {i+1} FAILED: {e}")
            import traceback
            traceback.print_exc()
        print()

    print("\n" + "=" * 60)
    print("ALL TESTS COMPLETE")
    print(f"Screenshots & HTML in: {OUTPUT_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
