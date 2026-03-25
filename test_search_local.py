"""
Local visual test — run 3 Chrome windows with Yandex search in parallel.
Browser is NOT headless, so you can see what's happening.

Usage: python test_search_local.py
"""
import sys
import os
import time
import random
import threading
from urllib.parse import quote_plus

sys.path.insert(0, os.path.dirname(__file__))

# Force non-headless BEFORE any imports that load settings
os.environ['YANDEX_BOT_BROWSER_HEADLESS'] = 'false'

from core.browser_manager import BrowserManager
from core.profile_generator import ProfileGenerator

# === CONFIG ===
THREADS = 3
# Test targets: 2 keywords for povoenke.ru + 1 for ecoinstrument.ru
TESTS = [
    {"domain": "povoenke.ru", "keyword": "военная ипотека 2026"},
    {"domain": "povoenke.ru", "keyword": "втб ипотека военная"},
    {"domain": "ecoinstrument.ru", "keyword": "ph метр"},
]
DOMAIN = TESTS[0]["domain"]  # default for backward compat
KEYWORDS = [t["keyword"] for t in TESTS]

PROXIES = [
    {"host": "95.31.178.33", "port": 4055, "username": "sqrtv2", "password": "21607141", "proxy_type": "http"},
    {"host": "95.31.178.33", "port": 4054, "username": "sqrtv2", "password": "21607141", "proxy_type": "http"},
    {"host": "95.31.170.9",  "port": 4097, "username": "sqrtv2", "password": "21607141", "proxy_type": "http"},
]

MAX_SEARCH_PAGES = 5


def search_and_find(thread_id, keyword, proxy_data, target_domain=None):
    """Open Chrome, search Yandex, look for domain in results."""
    global DOMAIN
    domain_to_find = target_domain or DOMAIN
    profile_name = f"Test-Local-{thread_id}"
    print(f"\n[T{thread_id}] 🚀 Starting: '{keyword}' → {domain_to_find} via proxy {proxy_data['host']}:{proxy_data['port']}")

    bm = BrowserManager()
    pg = ProfileGenerator()

    profile_data = pg.generate_profile(profile_name, is_mobile=False)
    profile_data.update({
        'language': 'ru-RU',
        'images_enabled': False,
    })

    browser_id = None
    try:
        # Override headless to False for visual debugging
        os.environ['YANDEX_BOT_BROWSER_HEADLESS'] = 'false'

        browser_id = bm.create_browser_session(profile_data, proxy_data)
        driver = bm.active_browsers[browser_id]
        driver.set_page_load_timeout(30)

        print(f"[T{thread_id}] 🌐 Opening ya.ru...")
        try:
            driver.get("https://ya.ru")
        except Exception as e:
            print(f"[T{thread_id}] ⏱️ ya.ru timeout: {e}")
            try:
                driver.execute_script("window.stop()")
            except:
                pass

        time.sleep(random.uniform(2, 4))
        print(f"[T{thread_id}] 📍 Current URL: {driver.current_url}")
        print(f"[T{thread_id}] 📍 Title: {driver.title}")

        # Check for captcha
        if 'showcaptcha' in driver.current_url.lower() or 'captcha' in driver.current_url.lower():
            print(f"[T{thread_id}] ⚠️ CAPTCHA on ya.ru! URL: {driver.current_url}")
            print(f"[T{thread_id}] 🛑 Keeping browser open for manual inspection...")
            input(f"[T{thread_id}] Press Enter after solving captcha (or to skip)...")

        # Search
        encoded = quote_plus(keyword)
        search_url = f"https://ya.ru/search/?text={encoded}"
        print(f"[T{thread_id}] 🔍 Searching: {keyword}")

        try:
            driver.get(search_url)
        except Exception as e:
            print(f"[T{thread_id}] ⏱️ Search timeout: {e}")
            try:
                driver.execute_script("window.stop()")
            except:
                pass

        time.sleep(random.uniform(3, 5))
        print(f"[T{thread_id}] 📍 Search URL: {driver.current_url[:120]}")

        # Check for captcha on search
        if 'showcaptcha' in driver.current_url.lower():
            print(f"[T{thread_id}] ⚠️ CAPTCHA on search! URL: {driver.current_url}")
            input(f"[T{thread_id}] Press Enter after solving captcha (or to skip)...")

        # Scan pages for domain
        for page_num in range(MAX_SEARCH_PAGES):
            print(f"\n[T{thread_id}] 📄 Scanning page {page_num + 1}...")

            # Get all links on page
            try:
                from selenium.webdriver.common.by import By
                links = driver.find_elements(By.CSS_SELECTOR, "a[href]")
                found_results = []
                for link in links:
                    try:
                        href = link.get_attribute("href") or ""
                        text = link.text.strip()[:80] if link.text else ""
                        if href and ('http' in href) and text:
                            # Check if it's a search result (not nav/header)
                            from urllib.parse import urlparse
                            parsed = urlparse(href)
                            domain_in_link = parsed.netloc.replace("www.", "")
                            # Skip yandex internal links
                            if 'yandex' in domain_in_link or 'ya.ru' in domain_in_link:
                                continue
                            if domain_in_link and len(text) > 10:
                                found_results.append((domain_in_link, text[:60], href[:100]))
                    except:
                        continue

                # Print results
                for idx, (dom, txt, href) in enumerate(found_results[:15], 1):
                    marker = " ✅ <<< FOUND!" if domain_to_find in dom else ""
                    print(f"[T{thread_id}]   #{idx}: {dom} — {txt}{marker}")

                # Check if our domain is there
                found = any(domain_to_find in r[0] for r in found_results)
                if found:
                    print(f"\n[T{thread_id}] 🎉 DOMAIN FOUND on page {page_num + 1}!")
                    break
                else:
                    print(f"[T{thread_id}] ❌ Domain not on page {page_num + 1}")

            except Exception as e:
                print(f"[T{thread_id}] Error scanning page: {e}")

            # Go to next page
            if page_num < MAX_SEARCH_PAGES - 1:
                try:
                    next_page_url = driver.current_url
                    from urllib.parse import urlparse as _up, parse_qs, urlencode
                    parsed = _up(next_page_url)
                    params = parse_qs(parsed.query)
                    params['p'] = [str(page_num + 1)]
                    new_query = urlencode({k: v[0] for k, v in params.items()})
                    next_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{new_query}"
                    print(f"[T{thread_id}] ➡️ Going to page {page_num + 2}...")
                    try:
                        driver.get(next_url)
                    except Exception:
                        try:
                            driver.execute_script("window.stop()")
                        except:
                            pass
                    time.sleep(random.uniform(2, 4))
                except Exception as e:
                    print(f"[T{thread_id}] Error navigating to next page: {e}")
                    break

        print(f"\n[T{thread_id}] 🏁 Done scanning {MAX_SEARCH_PAGES} pages.")
        print(f"[T{thread_id}] 🛑 Browser stays open. Press Ctrl+C to close all.")

        # Keep browser open
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"[T{thread_id}] ❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if browser_id and bm:
            try:
                bm.close_browser_session(browser_id)
            except:
                pass


def main():
    print("=" * 60)
    print(f"🔍 Local Yandex Search Test — {THREADS} threads")
    print(f"🎯 Target domain: {DOMAIN}")
    print(f"📋 Keywords: {KEYWORDS}")
    print("=" * 60)

    # Override headless
    os.environ['YANDEX_BOT_BROWSER_HEADLESS'] = 'false'

    threads = []
    for i in range(THREADS):
        test = TESTS[i % len(TESTS)]
        proxy = PROXIES[i % len(PROXIES)]
        t = threading.Thread(target=search_and_find, args=(i + 1, test["keyword"], proxy, test["domain"]), daemon=True)
        threads.append(t)

    for t in threads:
        t.start()
        time.sleep(2)  # Stagger starts

    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("\n\n🛑 Stopping all threads...")


if __name__ == "__main__":
    main()
