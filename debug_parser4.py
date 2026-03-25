"""Debug: test snippet click approach - card opens in side panel."""
from core.browser_manager import BrowserManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time, re

bm = BrowserManager()
pd = {
    "name": "Parser-Debug4",
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

    print("URL after load: " + driver.current_url[:150])

    # Find snippet titles (clickable links within search results)
    snippets = driver.find_elements(By.CSS_SELECTOR, ".search-business-snippet-view")
    print("Snippet count: " + str(len(snippets)))

    # Try click on title link inside snippet
    title_links = driver.find_elements(By.CSS_SELECTOR, ".search-business-snippet-view__title")
    print("Title links: " + str(len(title_links)))
    for i, t in enumerate(title_links[:5]):
        print("  " + str(i) + ": " + t.text[:80])

    if title_links and len(title_links) > 0:
        # Click on 1st snippet title
        print("\n=== CLICK SNIPPET 0 ===")
        title_links[0].click()
        time.sleep(5)
        url_after = driver.current_url
        print("URL after click: " + url_after[:150])

        # Check if card appeared - look for card elements
        h1s = driver.find_elements(By.CSS_SELECTOR, "h1.orgpage-header-view__header")
        print("orgpage h1 count: " + str(len(h1s)))
        for h in h1s:
            print("  H1: " + h.text[:100])

        cards = driver.find_elements(By.CSS_SELECTOR, ".business-card-view")
        print("business-card-view count: " + str(len(cards)))

        # Are snippets still visible?
        snippets_after = driver.find_elements(By.CSS_SELECTOR, ".search-business-snippet-view")
        print("Snippets still visible: " + str(len(snippets_after)))

        # Check phones on card
        phones = driver.find_elements(By.CSS_SELECTOR, "[class*='phones-view'] a[href^='tel:']")
        print("Phone links: " + str(len(phones)))

        # Try clicking expand phone button
        try:
            expand = driver.find_element(By.CSS_SELECTOR, ".card-phones-view__more, .orgpage-phones-view__more")
            print("Found phone expand button, clicking...")
            expand.click()
            time.sleep(1)
            phones2 = driver.find_elements(By.CSS_SELECTOR, "a[href^='tel:']")
            print("Phone links after expand: " + str(len(phones2)))
            for p in phones2:
                print("  " + (p.get_attribute("href") or ""))
        except Exception as e:
            print("No phone expand: " + str(e)[:80])

        # Now try to go BACK to search results
        print("\n=== GOING BACK ===")
        # Try browser back button
        driver.back()
        time.sleep(3)
        url_back = driver.current_url
        print("URL after back: " + url_back[:150])

        snippets_back = driver.find_elements(By.CSS_SELECTOR, ".search-business-snippet-view")
        print("Snippets after back: " + str(len(snippets_back)))

        if len(snippets_back) > 1:
            print("\n=== CLICK SNIPPET 1 ===")
            # Get new references
            title_links2 = driver.find_elements(By.CSS_SELECTOR, ".search-business-snippet-view__title")
            if len(title_links2) > 1:
                title_links2[1].click()
                time.sleep(5)
                h1s2 = driver.find_elements(By.CSS_SELECTOR, "h1.orgpage-header-view__header")
                for h in h1s2:
                    print("  H1: " + h.text[:100])
                print("Card loaded: " + str(len(h1s2) > 0))

finally:
    try:
        driver.quit()
    except:
        pass
