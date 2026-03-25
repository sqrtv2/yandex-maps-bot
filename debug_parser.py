"""Debug script to discover Yandex Maps page structure."""
import time
import re
from selenium.webdriver.common.by import By
from core.browser_manager import BrowserManager

bm = BrowserManager()
pd = {
    "name": "Parser-Debug",
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

    html = driver.page_source
    classes = re.findall(r'class="([^"]{10,80})"', html)
    unique_classes = set()
    for c in classes:
        for part in c.split():
            kws = ["search", "snippet", "serp", "card", "list", "scroll", "result", "orgpage", "business"]
            if any(k in part.lower() for k in kws):
                unique_classes.add(part)
    print("=== RELEVANT CLASSES ===")
    for c in sorted(unique_classes):
        print(c)

    print("\n=== ORG LINKS ===")
    links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/org/']")
    for i, l in enumerate(links[:8]):
        href = l.get_attribute("href") or ""
        txt = (l.text or "")[:80]
        print(f"{i}: {href[:150]} | {txt}")

    print("\n=== FIRST ORG CARD ===")
    if links:
        first_link = links[0].get_attribute("href")
        driver.get(first_link)
        time.sleep(5)

        # Find all element classes on the card page
        card_html = driver.page_source
        card_classes = re.findall(r'class="([^"]{10,80})"', card_html)
        card_unique = set()
        for c in card_classes:
            for part in c.split():
                kws = ["orgpage", "business", "card-title", "header", "contact", "phone",
                       "website", "address", "rating", "review", "schedule", "hours", "social"]
                if any(k in part.lower() for k in kws):
                    card_unique.add(part)
        for c in sorted(card_unique):
            print(c)

        print("\n=== H1 ELEMENTS ===")
        h1s = driver.find_elements(By.CSS_SELECTOR, "h1")
        for h in h1s:
            cls = h.get_attribute("class") or ""
            print(f"h1 class='{cls}' text='{h.text[:100]}'")

        print("\n=== TEL LINKS ===")
        tels = driver.find_elements(By.CSS_SELECTOR, "a[href^='tel:']")
        for t in tels:
            print(f"tel: {t.get_attribute('href')} | text: {t.text}")

        print("\n=== MAILTO LINKS ===")
        mails = driver.find_elements(By.CSS_SELECTOR, "a[href^='mailto:']")
        for m in mails:
            print(f"mailto: {m.get_attribute('href')}")

        print("\n=== EXTERNAL LINKS ===")
        ext = driver.find_elements(By.CSS_SELECTOR, "a[href*='http']")
        for e in ext[:15]:
            href = e.get_attribute("href") or ""
            if "yandex" not in href.lower() and "ya.ru" not in href.lower():
                print(f"ext: {href[:150]}")

finally:
    bm.close_browser(bid)
    print("\nDone.")
