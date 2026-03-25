"""
Local Yandex search test — NO PROXY, direct connection.
Headless=false so you can see the browser.
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

MAX_PAGES = 5


def run_test(test_config, test_num):
    domain = test_config["domain"]
    keyword = test_config["keyword"]

    print(f"\n{'='*60}")
    print(f"TEST #{test_num}: '{keyword}' -> {domain}")
    print(f"NO PROXY — direct connection")
    print(f"{'='*60}")

    with sync_playwright() as p:
        profile_dir = f"./browser_profiles/Test-Direct-{test_num}"
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
            # NO PROXY
            viewport={"width": 1366, "height": 768},
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            ignore_https_errors=True,
        )

        page = context.pages[0] if context.pages else context.new_page()

        # Step 1: ya.ru
        print(f"\n[{test_num}] Opening ya.ru...")
        try:
            page.goto("https://ya.ru", timeout=30000, wait_until="domcontentloaded")
        except Exception as e:
            print(f"[{test_num}] ya.ru timeout: {e}")

        time.sleep(2)
        print(f"[{test_num}] URL: {page.url}")
        print(f"[{test_num}] Title: {page.title()}")

        if "showcaptcha" in page.url.lower():
            print(f"[{test_num}] CAPTCHA! Solve it in the browser, then press Enter...")
            input()

        # Step 2: Search
        search_url = f"https://ya.ru/search/?text={quote_plus(keyword)}"
        print(f"\n[{test_num}] Searching: {keyword}")
        try:
            page.goto(search_url, timeout=30000, wait_until="domcontentloaded")
        except Exception as e:
            print(f"[{test_num}] Search timeout: {e}")

        time.sleep(3)
        current_url = page.url
        print(f"[{test_num}] URL: {current_url[:150]}")

        if "showcaptcha" in current_url.lower():
            print(f"[{test_num}] CAPTCHA on search! Solve it, then press Enter...")
            input()
            try:
                page.goto(search_url, timeout=30000, wait_until="domcontentloaded")
            except Exception:
                pass
            time.sleep(3)
            current_url = page.url

        # Step 3: Scan pages
        os.makedirs("screenshots", exist_ok=True)
        found_on_page = None
        for page_num in range(MAX_PAGES):
            print(f"\n[{test_num}] --- Page {page_num + 1} ---")

            page.screenshot(path=f"screenshots/direct_{test_num}_p{page_num+1}.png")

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

            for idx, (dom, txt, href) in enumerate(results[:20], 1):
                marker = " <<< FOUND!" if domain in dom else ""
                print(f"[{test_num}]   #{idx}: {dom} -- {txt}{marker}")

            if not results:
                print(f"[{test_num}]   (no results on this page)")
                try:
                    body = page.inner_text("body")[:300]
                    print(f"[{test_num}]   Body: {body[:200]}")
                except Exception:
                    pass

            if any(domain in r[0] for r in results):
                found_on_page = page_num + 1
                print(f"\n[{test_num}] DOMAIN '{domain}' FOUND on page {found_on_page}!")
                break
            else:
                print(f"[{test_num}] '{domain}' NOT on page {page_num + 1}")

            if page_num < MAX_PAGES - 1:
                parsed_url = urlparse(current_url)
                params = parse_qs(parsed_url.query)
                params['p'] = [str(page_num + 1)]
                new_query = urlencode({k: v[0] for k, v in params.items()})
                next_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}?{new_query}"
                print(f"[{test_num}] -> Page {page_num + 2}...")
                try:
                    page.goto(next_url, timeout=30000, wait_until="domcontentloaded")
                except Exception:
                    pass
                time.sleep(random.uniform(2, 4))

        print(f"\n[{test_num}] === RESULT: ", end="")
        if found_on_page:
            print(f"'{domain}' found on page {found_on_page} for '{keyword}'")
        else:
            print(f"'{domain}' NOT found in {MAX_PAGES} pages for '{keyword}'")

        print(f"[{test_num}] Browser open 10s for inspection...")
        time.sleep(10)
        context.close()


def main():
    os.makedirs("screenshots", exist_ok=True)
    print("Local Yandex Search — NO PROXY")
    print(f"Tests: {len(TESTS)}\n")

    for i, test in enumerate(TESTS):
        try:
            run_test(test, i + 1)
        except Exception as e:
            print(f"\nTest {i+1} FAILED: {e}")
            import traceback
            traceback.print_exc()

    print("\nALL TESTS DONE")


if __name__ == "__main__":
    main()
