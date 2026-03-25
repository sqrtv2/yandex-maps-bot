"""Quick test: search Yandex via proxy with Playwright and check if domain is in results"""
import sys
import time

# Use the project's own browser_manager to test a real search
from playwright.sync_api import sync_playwright

PROXY = {"server": "http://95.31.170.9:4097", "username": "sqrtv2", "password": "21607141"}
KEYWORD = "военная ипотека втб"
DOMAIN = "povoenke.ru"
MAX_PAGES = 3

def test_search():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                  "--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            proxy=PROXY,
            locale="ru-RU",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = context.new_page()
        
        print(f"Searching: '{KEYWORD}' → looking for {DOMAIN}")
        print(f"Proxy: {PROXY['server']}")
        print()
        
        # Navigate to search
        from urllib.parse import quote_plus
        url = f"https://ya.ru/search/?text={quote_plus(KEYWORD)}&lr=213"
        page.goto(url, timeout=40000)
        time.sleep(3)
        
        title = page.title()
        cur_url = page.url
        print(f"Title: {title}")
        print(f"URL: {cur_url[:120]}")
        
        # Check for captcha
        if "showcaptcha" in cur_url.lower() or "captcha" in cur_url.lower():
            print("\n⚠️ CAPTCHA detected! Proxy may be blocked by Yandex")
            # Take screenshot
            page.screenshot(path="/app/test_search_screenshot.png")
            print("Screenshot saved to /app/test_search_screenshot.png")
            browser.close()
            return
        
        if "Вы не робот" in (page.content()[:3000]):
            print("\n⚠️ 'Вы не робот' detected!")
            browser.close()
            return
        
        # Parse results
        found = False
        for pg in range(1, MAX_PAGES + 1):
            print(f"\n--- Page {pg} ---")
            results = page.query_selector_all("[data-cid]")
            organic = []
            for r in results:
                try:
                    links = r.query_selector_all("a[href]")
                    for link in links:
                        href = link.get_attribute("href") or ""
                        if href.startswith("http") and "ya.ru" not in href and "yandex" not in href:
                            from urllib.parse import urlparse
                            domain_parsed = urlparse(href).netloc.replace("www.", "")
                            title_text = link.inner_text()[:60] if link.inner_text() else ""
                            if domain_parsed and domain_parsed not in [o[1] for o in organic]:
                                organic.append((title_text, domain_parsed, href))
                            break
                except:
                    continue
            
            for i, (t, d, h) in enumerate(organic[:12], 1):
                marker = " ← ✅ FOUND!" if DOMAIN in d else ""
                print(f"  #{i}: {t[:50]} → {d}{marker}")
                if DOMAIN in d:
                    found = True
            
            if found:
                break
            
            if pg < MAX_PAGES:
                # Next page
                next_link = page.query_selector(f'a[href*="p={pg}"]')
                if not next_link:
                    next_link = page.query_selector(f'a.Pager-Item_type_next')
                if next_link:
                    next_link.click()
                    time.sleep(3)
                else:
                    print("  No next page link found")
                    break
        
        if found:
            print(f"\n✅ {DOMAIN} FOUND in search results!")
        else:
            print(f"\n❌ {DOMAIN} NOT FOUND in {MAX_PAGES} pages")
        
        browser.close()

if __name__ == "__main__":
    test_search()
