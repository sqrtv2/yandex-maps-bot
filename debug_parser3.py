"""Debug: test exact parser flow with _collect_company_links."""
from core.browser_manager import BrowserManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time, re

bm = BrowserManager()
pd = {
    "name": "Parser-Debug3",
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

    # Replicate _collect_company_links logic exactly
    seen_oids = set()
    links = []
    elements = driver.find_elements(By.CSS_SELECTOR, "a[href*='/org/']")
    print("Total a[href*='/org/'] elements: " + str(len(elements)))

    for el in elements:
        href = el.get_attribute("href") or ""
        org_match = re.search(r"/org/[^/]+/(\d+)", href)
        if not org_match:
            continue
        org_id = org_match.group(1)
        if org_id in seen_oids:
            continue

        match = re.search(r"(/org/[^/]+/\d+/?)", href)
        if not match:
            continue

        base = href.split("/org/")[0]
        clean_url = base + match.group(1)
        if not clean_url.endswith("/"):
            clean_url += "/"

        seen_oids.add(org_id)
        links.append((clean_url, org_id))
        print("CLEAN URL: " + clean_url[:150] + " | ID: " + org_id)

    # Try navigating to first 3 clean URLs
    for i, (url, oid) in enumerate(links[:3]):
        print("\n=== NAV TO #" + str(i) + ": " + url[:150] + " ===")
        driver.get(url)
        time.sleep(6)

        print("Current URL: " + driver.current_url[:150])

        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR,
                    "h1.orgpage-header-view__header, "
                    "div.business-card-view, "
                    "div[class*='orgpage-header-view']"))
            )
            print("CARD LOADED OK")
        except:
            print("CARD TIMEOUT - NOT LOADED")

        h1s = driver.find_elements(By.CSS_SELECTOR, "h1")
        for h in h1s:
            if h.text.strip():
                print("H1: " + h.text.strip()[:100])

        # Check for cookie/GDPR popup blocking
        gdpr = driver.find_elements(By.CSS_SELECTOR, "[class*='gdpr']")
        print("GDPR elements: " + str(len(gdpr)))

finally:
    try:
        driver.quit()
    except:
        pass
