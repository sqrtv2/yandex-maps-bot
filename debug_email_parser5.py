"""Debug v5: Intercept XHR via CDP to find org data with email."""
import time
import re
import json
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options

opts = Options()
opts.add_argument('--headless=new')
opts.add_argument('--no-sandbox')
opts.add_argument('--disable-dev-shm-usage')
opts.add_argument('--lang=ru')
opts.binary_location = '/usr/bin/chromium'
opts.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
driver = webdriver.Chrome(options=opts)
driver.set_window_size(1920, 1080)

# Enable CDP network
driver.execute_cdp_cmd('Network.enable', {})

# Search and click (the way the parser actually works)
print('=== SEARCHING ===')
driver.get('https://yandex.ru/maps/?text=стоматология+москва&lang=ru')
time.sleep(8)

# Check what URL we actually landed on
print(f'Current URL: {driver.current_url}')
page_title = driver.title
print(f'Page title: {page_title}')

# Get page text
body_text = driver.execute_script("return document.body.innerText.substring(0, 500);")
print(f'Body start: {body_text[:200]}')

# Check if we're on the right page 
snippets = driver.find_elements(By.CSS_SELECTOR, '.search-business-snippet-view')
print(f'Snippets found: {len(snippets)}')

if not snippets:
    # Maybe redirected - check alternative selectors
    snippets = driver.find_elements(By.CSS_SELECTOR, '.search-snippet-view')
    print(f'Alt snippets: {len(snippets)}')
    # Try any search results
    any_results = driver.find_elements(By.CSS_SELECTOR, '[class*="search"][class*="snippet"]')
    print(f'Any results: {len(any_results)}')

# Click on first snippet
if snippets:
    try:
        title_el = snippets[0].find_element(By.CSS_SELECTOR, '.search-business-snippet-view__title, a')
        name = title_el.text
        print(f'\nClicking: {name}')
        title_el.click()
        time.sleep(5)
    except Exception as ex:
        print(f'Click error: {ex}')

# Get all network logs
print('\n=== NETWORK LOGS (org-related) ===')
logs = driver.get_log('performance')
org_requests = []
for entry in logs:
    try:
        msg = json.loads(entry['message'])['message']
        if msg['method'] == 'Network.responseReceived':
            url = msg['params']['response']['url']
            if any(x in url for x in ['orgpage', 'fetchOrg', 'business', 'fetchByIds', 'orgInfo']):
                org_requests.append({
                    'url': url[:200],
                    'requestId': msg['params']['requestId'],
                    'status': msg['params']['response']['status']
                })
    except (KeyError, json.JSONDecodeError):
        pass

print(f'Found {len(org_requests)} org-related requests')
for req in org_requests:
    print(f'  [{req["status"]}] {req["url"]}')
    
    # Try to get response body
    try:
        body = driver.execute_cdp_cmd('Network.getResponseBody', {'requestId': req['requestId']})
        response_text = body.get('body', '')
        if response_text:
            print(f'    Response length: {len(response_text)}')
            # Search for email
            emails = re.findall(r'[a-zA-Z0-9_.+-]{3,}@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}', response_text)
            service_emails = ['maps.yandex', 'yandex-team', 'support.yandex']
            real_emails = [e for e in emails if not any(x in e.lower() for x in service_emails)]
            if real_emails:
                print(f'    EMAILS FOUND: {real_emails}')
            
            # Show contact-related data
            for key in ['"email"', '"Emails"', '"contacts"', '"Phones"', '"Links"', '"Urls"']:
                idx = response_text.find(key)
                if idx > -1:
                    print(f'    Key "{key}": ...{response_text[max(0,idx-50):idx+300]}...')
            
            # If JSON, dump structure
            try:
                data = json.loads(response_text)
                if isinstance(data, dict):
                    print(f'    Top keys: {list(data.keys())[:15]}')
            except:
                pass
    except Exception as ex:
        print(f'    Error getting body: {ex}')

# Also check ALL requests with response body for email
print('\n=== SCANNING ALL XHR FOR EMAILS ===')
for entry in logs:
    try:
        msg = json.loads(entry['message'])['message']
        if msg['method'] == 'Network.responseReceived':
            content_type = msg['params']['response'].get('headers', {}).get('content-type', '')
            if 'json' in content_type or 'javascript' in content_type:
                req_id = msg['params']['requestId']
                try:
                    body = driver.execute_cdp_cmd('Network.getResponseBody', {'requestId': req_id})
                    text = body.get('body', '')
                    if '"email"' in text.lower() and 'billingContact' not in text:
                        url = msg['params']['response']['url'][:100]
                        idx = text.lower().find('"email"')
                        print(f'  URL: {url}')
                        print(f'  Context: {text[max(0,idx-100):idx+300]}')
                except:
                    pass
    except:
        pass

# Let's also check - does the CARD panel have ANY contact info visible?
print('\n=== CARD PANEL CONTACTS ===')
contact_texts = driver.execute_script("""
    var results = [];
    // Try various selectors for the card panel
    var selectors = [
        '.business-contacts-view',
        '.orgpage-header-view__contacts',
        '.card-phones-view',
        '.business-urls-view',
        '.business-card-view',
    ];
    for (var sel of selectors) {
        var el = document.querySelector(sel);
        if (el) {
            results.push({selector: sel, text: el.innerText.substring(0, 300), html: el.innerHTML.substring(0, 500)});
        }
    }
    return JSON.stringify(results);
""")
try:
    contacts = json.loads(contact_texts)
    for c in contacts:
        print(f'\n  {c["selector"]}:')
        print(f'    Text: {c["text"]}')
        print(f'    HTML: {c["html"][:300]}')
except:
    print(f'  Raw: {contact_texts}')

driver.quit()
print('\nDone!')
