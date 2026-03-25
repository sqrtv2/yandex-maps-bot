"""Debug: compare clicking snippet vs direct navigation."""
from core.browser_manager import BrowserManager
from selenium.webdriver.common.by import By
import time

bm = BrowserManager()
pd = {
    "name": "Parser-Debug2",
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

    # Collect org links
    links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/org/']")
    hrefs = []
    for l in links[:5]:
        h = l.get_attribute("href") or ""
        hrefs.append(h)
        print("LINK: " + h[:150])

    # Click on snippet
    print("\n=== CLICK ON SNIPPET ===")
    snippets = driver.find_elements(By.CSS_SELECTOR, ".search-business-snippet-view")
    print("snippets count: " + str(len(snippets)))

    if snippets:
        snippets[0].click()
        time.sleep(6)
        print("URL: " + driver.current_url[:150])

        h1s = driver.find_elements(By.CSS_SELECTOR, "h1")
        for h in h1s:
            if h.text.strip():
                print("H1: " + h.text.strip()[:100])

        headers = driver.find_elements(By.CSS_SELECTOR, ".orgpage-header-view__header")
        print("orgpage headers: " + str(len(headers)))

        cards = driver.find_elements(By.CSS_SELECTOR, ".business-card-view")
        print("business cards: " + str(len(cards)))

        phones = driver.find_elements(By.CSS_SELECTOR, "[class*='phone']")
        for p in phones[:5]:
            cls = p.get_attribute("class") or ""
            print("phone: " + cls[:80] + " | " + p.text[:50])

    # Now navigate directly
    if hrefs:
        print("\n=== DIRECT NAV ===")
        driver.get(hrefs[0])
        time.sleep(8)
        print("URL: " + driver.current_url[:150])
        print("Title: " + driver.title[:100])

        h1s = driver.find_elements(By.CSS_SELECTOR, "h1")
        for h in h1s:
            if h.text.strip():
                print("H1: " + h.text.strip()[:100])

        headers = driver.find_elements(By.CSS_SELECTOR, ".orgpage-header-view__header")
        print("orgpage headers: " + str(len(headers)))

        cards = driver.find_elements(By.CSS_SELECTOR, ".business-card-view")
        print("business cards: " + str(len(cards)))

        # Check if there's a captcha or any blocking
        body_text = driver.find_element(By.TAG_NAME, "body").text[:500]
        print("Body text: " + body_text[:300])

finally:
    try:
        driver.quit()
    except:
        pass
