"""Debug script v2: find where email is hidden on Yandex Maps card."""
import time
import re
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

opts = Options()
opts.add_argument('--headless=new')
opts.add_argument('--no-sandbox')
opts.add_argument('--disable-dev-shm-usage')
opts.add_argument('--lang=ru')
opts.add_argument('--accept-lang=ru-RU,ru')
opts.binary_location = '/usr/bin/chromium'
driver = webdriver.Chrome(options=opts)
driver.set_window_size(1920, 1080)

def check_email_on_page(url, label):
    print(f'\n{"="*60}')
    print(f'=== {label}: {url} ===')
    print(f'{"="*60}')
    driver.get(url)
    time.sleep(6)

    # Try to click "Подробнее об организации" / "Info about organization"
    expand_selectors = [
        "//span[contains(text(),'Подробнее')]",
        "//span[contains(text(),'подробнее')]",
        "//a[contains(text(),'Подробнее')]",
        "//span[contains(text(),'Info about')]",
        "//span[contains(text(),'Информация')]",
        "//div[contains(@class,'more-info')]",
        ".business-features-view__more-info",
    ]
    for sel in expand_selectors:
        try:
            if sel.startswith('.') or sel.startswith('['):
                els = driver.find_elements(By.CSS_SELECTOR, sel)
            else:
                els = driver.find_elements(By.XPATH, sel)
            for el in els:
                if el.is_displayed():
                    print(f'  Clicking expand: {sel} => text: {el.text[:80]}')
                    el.click()
                    time.sleep(3)
                    break
        except Exception:
            pass

    html = driver.page_source

    # All card-feature-view elements (these are the contact rows)
    features = driver.find_elements(By.CSS_SELECTOR, '.card-feature-view')
    print(f'\n--- card-feature-view elements: {len(features)} ---')
    for f in features:
        cls = f.get_attribute('class') or ''
        txt = f.text.replace('\n', ' | ')[:200] if f.text else '(empty)'
        # Check inner links
        links = f.find_elements(By.CSS_SELECTOR, 'a[href]')
        link_info = ''
        for lnk in links:
            href = lnk.get_attribute('href') or ''
            if '@' in href or 'mail' in href.lower():
                link_info += f' [LINK: {href}]'
        print(f'  [{cls[:80]}] => {txt}{link_info}')

    # Check for email icon (SVG or class based)
    print('\n--- Elements with "mail" or "email" in class/aria ---')
    mail_els = driver.find_elements(By.CSS_SELECTOR, '[class*="mail"], [class*="email"], [aria-label*="mail"], [aria-label*="email"], [data-type*="email"]')
    for el in mail_els:
        cls = el.get_attribute('class') or ''
        aria = el.get_attribute('aria-label') or ''
        dtype = el.get_attribute('data-type') or ''
        txt = el.text[:120] if el.text else '(empty)'
        tag = el.tag_name
        print(f'  <{tag} class="{cls[:80]}" aria="{aria}" data-type="{dtype}"> => {txt}')

    # Check page source for email patterns near "contact" or "email" words
    # Look for structured data / JSON-LD with email
    json_ld = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
    for jld in json_ld:
        if 'email' in jld.lower() or '@' in jld:
            print(f'\n--- JSON-LD with email: ---')
            print(jld[:500])

    # Check meta tags
    meta_els = driver.find_elements(By.CSS_SELECTOR, 'meta[content*="@"]')
    for m in meta_els:
        print(f'  META: {m.get_attribute("name") or m.get_attribute("property")} = {m.get_attribute("content")}')

    # Check data attributes for email
    all_els_with_data = driver.find_elements(By.CSS_SELECTOR, '[data-email], [data-value*="@"]')
    for el in all_els_with_data:
        print(f'  DATA: {el.get_attribute("data-email") or el.get_attribute("data-value")}')

    # Try Yandex internal API - check network requests / embedded data
    # The org data is often embedded in window.__PRELOADED_DATA or similar
    preloaded = driver.execute_script("""
        try {
            // Try various known Yandex state containers
            var data = window.__PRELOADED_DATA || window.__INITIAL_STATE__ || window.__DATA__;
            if (data) return JSON.stringify(data).substring(0, 5000);
        } catch(e) {}
        return null;
    """)
    if preloaded:
        # Search for email in preloaded data
        email_matches = re.findall(r'[\w.+-]+@[\w-]+\.[\w.]+', preloaded)
        real_emails = [e for e in email_matches if len(e.split('@')[0]) > 2]
        if real_emails:
            print(f'\n--- Emails in preloaded data: ---')
            for e in set(real_emails):
                print(f'  {e}')

    # Try JavaScript extraction of org data from React state
    org_data = driver.execute_script("""
        try {
            // Search all script tags for embedded org JSON
            var scripts = document.querySelectorAll('script');
            for (var s of scripts) {
                var text = s.textContent || '';
                if (text.includes('"email"') || text.includes('"Email"')) {
                    // Extract the relevant snippet
                    var idx = text.indexOf('"email"');
                    if (idx === -1) idx = text.indexOf('"Email"');
                    if (idx > -1) {
                        return text.substring(Math.max(0, idx-100), idx+200);
                    }
                }
            }
        } catch(e) { return 'error: ' + e.message; }
        return null;
    """)
    if org_data:
        print(f'\n--- Script with "email" field: ---')
        print(org_data[:500])

    # Parse Yandex Maps API response embedded in page
    api_data = driver.execute_script("""
        try {
            var scripts = document.querySelectorAll('script');
            for (var s of scripts) {
                var t = s.textContent || '';
                // Look for org metadata patterns
                if (t.includes('ContactInfo') || t.includes('contactInfo') || t.includes('Emails')) {
                    var idx = t.search(/contact|email/i);
                    if (idx > -1) {
                        return t.substring(Math.max(0, idx-200), idx+500);
                    }
                }
            }
        } catch(e) { return 'error: ' + e.message; }
        return null;
    """)
    if api_data:
        print(f'\n--- ContactInfo/Email in script: ---')
        print(api_data[:500])

    # Check if there's an email in the expanded features section
    feature_texts = driver.execute_script("""
        var result = [];
        var els = document.querySelectorAll('.business-features-view__valued-item, .business-features-view__item, .card-feature-view__content');
        for (var el of els) {
            var text = el.textContent.trim();
            if (text && text.includes('@')) {
                result.push(text);
            }
        }
        return result;
    """)
    if feature_texts:
        print(f'\n--- Feature items with @: ---')
        for ft in feature_texts:
            print(f'  {ft}')


# Test with companies known to have emails
# 1. Try a search page approach (like the real parser does)
print('\n\n*** SEARCH-BASED APPROACH ***')
driver.get('https://yandex.ru/maps/?text=стоматология+москва')
time.sleep(8)

# Click first result
try:
    snippets = driver.find_elements(By.CSS_SELECTOR, '.search-business-snippet-view')
    print(f'Found {len(snippets)} snippets')
    if snippets:
        title = snippets[0].find_element(By.CSS_SELECTOR, '.search-business-snippet-view__title')
        print(f'Clicking: {title.text}')
        title.click()
        time.sleep(5)
        check_email_on_page(driver.current_url, 'First search result (stomatology)')
except Exception as ex:
    print(f'Search approach error: {ex}')

# Direct company pages
check_email_on_page('https://yandex.ru/maps/org/gemotest/237720251425/', 'Gemotest lab')
check_email_on_page('https://yandex.ru/maps/org/invitro/1019624886/', 'Invitro lab')

driver.quit()
print('\n\nDone!')
