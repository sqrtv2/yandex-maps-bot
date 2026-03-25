"""Debug script: check how email appears on Yandex Maps company card."""
import time
import re
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options

opts = Options()
opts.add_argument('--headless=new')
opts.add_argument('--no-sandbox')
opts.add_argument('--disable-dev-shm-usage')
opts.binary_location = '/usr/bin/chromium'
driver = webdriver.Chrome(options=opts)
driver.set_window_size(1920, 1080)

# Open a specific company on Yandex Maps that likely has email
# (MedSwiss Moscow - large clinic chain, should have contacts)
driver.get('https://yandex.ru/maps/org/medswiss/1260673964/')
time.sleep(8)

html = driver.page_source

# Search for email-like patterns in raw HTML
emails_in_html = re.findall(r'[\w.+-]+@[\w-]+\.[\w.]+', html)
print('=== EMAILS FOUND IN HTML ===')
for e in emails_in_html:
    print(f'  {e}')

# Search for mailto links
mailto_count = html.count('mailto:')
print(f'\n=== mailto: count: {mailto_count} ===')

# Search for @ symbol context (skip CSS media queries)
at_positions = [m.start() for m in re.finditer('@', html)]
print(f'\n=== @ positions count: {len(at_positions)} ===')
for pos in at_positions[:15]:
    snippet = html[max(0, pos-80):pos+80].replace('\n', ' ')
    # Skip CSS/media query noise
    if 'media' in snippet.lower() or 'keyframes' in snippet.lower():
        continue
    print(f'  ...{snippet}...')

# Check for contact blocks
print('\n=== CONTACT BLOCKS ===')
selectors = [
    '.business-contacts-view__block',
    '.orgpage-contacts-view__block',
    '.card-feature-view',
    '.business-card-feature-view',
    '[class*="contacts"]',
    '[class*="email"]',
    '[class*="mail"]',
]
for sel in selectors:
    try:
        elements = driver.find_elements(By.CSS_SELECTOR, sel)
        if elements:
            print(f'\n  Selector: {sel} -> {len(elements)} elements')
            for el in elements[:10]:
                cls = el.get_attribute('class') or ''
                txt = el.text[:200] if el.text else '(empty)'
                print(f'    [{cls}] => {txt}')
    except Exception as ex:
        print(f'  Selector {sel} error: {ex}')

# Check all links on page
print('\n=== ALL LINKS WITH MAILTO OR EMAIL ===')
all_links = driver.find_elements(By.CSS_SELECTOR, 'a[href]')
for link in all_links:
    href = link.get_attribute('href') or ''
    if 'mailto' in href.lower() or '@' in href:
        print(f'  href={href}  text={link.text}')

# Try another company known to have email
print('\n\n=== TRYING ANOTHER COMPANY ===')
driver.get('https://yandex.ru/maps/org/laboratoriya_gemotest/1124715036/')
time.sleep(8)

html2 = driver.page_source
emails2 = re.findall(r'[\w.+-]+@[\w-]+\.[\w.]+', html2)
print(f'Emails found: {emails2}')
print(f'mailto count: {html2.count("mailto:")}')

# Check contact blocks for Gemotest
for sel in selectors:
    try:
        elements = driver.find_elements(By.CSS_SELECTOR, sel)
        if elements:
            print(f'\n  Selector: {sel} -> {len(elements)} elements')
            for el in elements[:10]:
                cls = el.get_attribute('class') or ''
                txt = el.text[:200] if el.text else '(empty)'
                print(f'    [{cls}] => {txt}')
    except Exception:
        pass

driver.quit()
print('\nDone!')
