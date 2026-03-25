"""Debug: analyze card structure after snippet click."""
from core.browser_manager import BrowserManager
from selenium.webdriver.common.by import By
import time, re

bm = BrowserManager()
pd = {
    "name": "Parser-Debug5",
    "user_agent": None,
    "viewport_width": 1920,
    "viewport_height": 1080,
    "timezone": "Europe/Moscow",
    "language": "ru-RU",
}
bid = bm.create_browser_session(pd, None)
driver = bm.active_browsers[bid]

try:
    driver.get("https://yandex.ru/maps/?text=медцентр+Москва")
    time.sleep(10)

    # Click first snippet
    titles = driver.find_elements(By.CSS_SELECTOR, ".search-business-snippet-view__title")
    if titles:
        titles[0].click()
        time.sleep(5)

        print("=== CARD STRUCTURE ===")
        # Find ALL elements with relevant classes
        html = driver.page_source
        classes = re.findall(r'class="([^"]{10,120})"', html)
        card_classes = set()
        for c in classes:
            for part in c.split():
                kws = ["card-title", "card-phones", "business-contacts",
                       "business-urls", "orgpage-header", "business-card",
                       "card-section", "card-feature", "working-status"]
                if any(k in part.lower() for k in kws):
                    card_classes.add(part)
        for c in sorted(card_classes):
            print(c)

        # Look for the company name
        print("\n=== NAME ===")
        name_selectors = [
            "h1.orgpage-header-view__header",
            ".business-card-view h1",
            ".card-title-view__title",
            "[class*='card-title']",
            ".orgpage-header-view__header",
        ]
        for sel in name_selectors:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            for e in els:
                if e.text.strip():
                    print(sel + " -> " + e.text.strip()[:100])

        # All h1 elements
        print("\n=== ALL H1 ===")
        h1s = driver.find_elements(By.CSS_SELECTOR, "h1")
        for h in h1s:
            cls = h.get_attribute("class") or ""
            print("h1." + cls + " -> " + h.text.strip()[:100])

        # All text inside business-card-view
        print("\n=== CARD TEXT ===")
        cards = driver.find_elements(By.CSS_SELECTOR, ".business-card-view")
        if cards:
            card_text = cards[0].text
            print(card_text[:800])

        # Phone elements
        print("\n=== PHONE ELEMENTS ===")
        phone_els = driver.find_elements(By.CSS_SELECTOR,
            "[class*='phone'], a[href^='tel:']")
        for p in phone_els[:10]:
            tag = p.tag_name
            cls = (p.get_attribute("class") or "")[:80]
            href = (p.get_attribute("href") or "")
            txt = p.text[:60]
            print(tag + " | " + cls + " | " + href[:60] + " | " + txt)

        # Website/URL elements
        print("\n=== URL ELEMENTS ===")
        url_els = driver.find_elements(By.CSS_SELECTOR,
            "[class*='urls-view'], [class*='business-contacts-view__link']")
        for u in url_els[:5]:
            print(u.tag_name + " | " + (u.get_attribute("class") or "")[:80] + " | " + u.text[:80])

        # Social links
        print("\n=== SOCIAL/EXTERNAL LINKS ===")
        ext = driver.find_elements(By.CSS_SELECTOR, "a[href]")
        for e in ext:
            href = (e.get_attribute("href") or "").lower()
            if any(k in href for k in ["vk.com", "t.me", "wa.me", "whatsapp", "instagram", "ok.ru", "mailto:", "tel:"]):
                print(e.get_attribute("href")[:120])

        # Address
        print("\n=== ADDRESS ===")
        addr_els = driver.find_elements(By.CSS_SELECTOR,
            "[class*='address'], [class*='orgpage-header-view__address']")
        for a in addr_els[:5]:
            if a.text.strip():
                cls = (a.get_attribute("class") or "")[:80]
                print(cls + " -> " + a.text.strip()[:120])

finally:
    try:
        driver.quit()
    except:
        pass
