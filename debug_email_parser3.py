"""Debug v3: Deep search for email in Yandex Maps page data."""
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
driver = webdriver.Chrome(options=opts)
driver.set_window_size(1920, 1080)

# Company that definitely has email listed: try a hotel
driver.get('https://yandex.ru/maps/?text=гостиница+москва')
time.sleep(8)

# Click first result
snippets = driver.find_elements(By.CSS_SELECTOR, '.search-business-snippet-view')
print(f'Found {len(snippets)} snippets')
if snippets:
    title_el = snippets[0].find_element(By.CSS_SELECTOR, '.search-business-snippet-view__title')
    print(f'Clicking: {title_el.text}')
    title_el.click()
    time.sleep(5)

# Get full page source and search for email indicators
html = driver.page_source

# 1. Search for common email patterns in ALL scripts
print('\n=== SEARCHING SCRIPTS FOR EMAIL DATA ===')
scripts = driver.find_elements(By.CSS_SELECTOR, 'script')
found_email_in_script = False
for i, s in enumerate(scripts):
    try:
        content = s.get_attribute('textContent') or ''
        if not content:
            continue
        # Look for email-like strings (longer than 2 chars before @)
        real_emails = re.findall(r'[a-zA-Z0-9_.+-]{3,}@[a-zA-Z0-9-]+\.[a-zA-Z.]+', content)
        # Filter out yandex service emails
        real_emails = [e for e in real_emails if not any(x in e.lower() for x in ['maps.yandex', 'yandex-team', 'support.yandex'])]
        if real_emails:
            print(f'  Script #{i}: found emails: {real_emails[:5]}')
            # Show context around first email
            for em in real_emails[:2]:
                idx = content.find(em)
                if idx > -1:
                    ctx = content[max(0, idx-200):idx+200]
                    print(f'    Context: ...{ctx[:400]}...')
            found_email_in_script = True
    except Exception:
        pass

if not found_email_in_script:
    print('  No real emails found in scripts')

# 2. Check for Yandex Maps state in window object
print('\n=== CHECKING WINDOW STATE ===')
state_check = driver.execute_script("""
    var results = {};
    // Check common state containers
    var keys = Object.keys(window);
    var stateKeys = keys.filter(k => k.startsWith('__') || k.includes('STATE') || k.includes('DATA') || k.includes('INIT'));
    results.stateKeys = stateKeys;
    
    // Check for ymaps state
    if (window.ymaps) results.hasYmaps = true;
    
    // Search document for data-* attributes with email
    var allEls = document.querySelectorAll('*');
    var emailEls = [];
    for (var el of allEls) {
        for (var attr of el.attributes) {
            if (attr.value && attr.value.includes('@') && attr.value.includes('.') && attr.name.startsWith('data-')) {
                emailEls.push({tag: el.tagName, attr: attr.name, val: attr.value.substring(0, 200)});
            }
        }
    }
    results.emailDataAttrs = emailEls;
    
    return JSON.stringify(results);
""")
print(f'State: {state_check}')

# 3. Check the network/XHR for org API calls
# Yandex Maps loads org data via internal API - let's intercept
print('\n=== CHECKING FOR ORG API DATA IN PAGE ===')
org_api_data = driver.execute_script("""
    // Search for org data patterns in all script content
    var scripts = document.querySelectorAll('script');
    var results = [];
    for (var s of scripts) {
        var t = s.textContent || '';
        // Look for email in contact blocks
        var patterns = ['"Emails"', '"emails"', '"email":', '"contactEmail"', '"mail":', 'email_address', 'emailAddress'];
        for (var p of patterns) {
            var idx = t.indexOf(p);
            if (idx > -1) {
                var ctx = t.substring(Math.max(0, idx-300), idx+300);
                // Skip Yandex Pay SDK
                if (ctx.includes('pay.yandex.ru') || ctx.includes('billingContact')) continue;
                results.push({pattern: p, context: ctx});
            }
        }
    }
    return JSON.stringify(results);
""")
try:
    parsed = json.loads(org_api_data)
    if parsed:
        print(f'Found {len(parsed)} email-related patterns:')
        for p in parsed:
            print(f'  Pattern: {p["pattern"]}')
            print(f'  Context: {p["context"][:300]}')
    else:
        print('No email patterns found in scripts')
except:
    print(f'Raw: {org_api_data}')

# 4. Try fetching Yandex Maps org API directly
print('\n=== TESTING YANDEX MAPS ORG API ===')
# Get current org ID from URL
current_url = driver.current_url
org_id_match = re.search(r'/org/[^/]+/(\d+)', current_url)
if org_id_match:
    org_id = org_id_match.group(1)
    print(f'Org ID: {org_id}')
    
    # Try the Yandex Maps Business API
    api_urls = [
        f'https://yandex.ru/maps/api/business/fetchByIds?ids={org_id}&lang=ru_RU',
        f'https://yandex.ru/maps/api/orgpage/fetchOrg?orgId={org_id}&lang=ru_RU',
    ]
    
    for api_url in api_urls:
        try:
            driver.execute_script(f'window.open("{api_url}")')
            time.sleep(1)
            handles = driver.window_handles
            driver.switch_to.window(handles[-1])
            time.sleep(3)
            body = driver.find_element(By.TAG_NAME, 'body').text
            if body:
                # Parse as JSON
                try:
                    data = json.loads(body)
                    # Search for email recursively
                    def find_emails(obj, path=''):
                        results = []
                        if isinstance(obj, dict):
                            for k, v in obj.items():
                                if 'email' in k.lower() or 'mail' in k.lower():
                                    results.append(f'{path}.{k} = {v}')
                                results.extend(find_emails(v, f'{path}.{k}'))
                        elif isinstance(obj, list):
                            for i, item in enumerate(obj):
                                results.extend(find_emails(item, f'{path}[{i}]'))
                        elif isinstance(obj, str) and '@' in obj and '.' in obj:
                            results.append(f'{path} = {obj}')
                        return results
                    
                    emails = find_emails(data)
                    print(f'\n  API {api_url[:80]}:')
                    if emails:
                        for e in emails:
                            print(f'    {e}')
                    else:
                        print(f'    No email fields found')
                        # Show what contact info IS available
                        data_str = json.dumps(data, ensure_ascii=False)
                        for key in ['phone', 'Phone', 'contact', 'Contact', 'address', 'url', 'website', 'Website']:
                            if key.lower() in data_str.lower():
                                # Find context
                                idx = data_str.lower().find(key.lower())
                                print(f'    Has "{key}": ...{data_str[max(0,idx-50):idx+150]}...')
                except json.JSONDecodeError:
                    if '@' in body and '.' in body:
                        emails = re.findall(r'[a-zA-Z0-9_.+-]{3,}@[a-zA-Z0-9-]+\.[a-zA-Z.]+', body)
                        real = [e for e in emails if 'yandex-team' not in e and 'maps.yandex' not in e]
                        print(f'  Non-JSON response with emails: {real[:10]}')
                    else:
                        print(f'  Non-JSON response (first 300): {body[:300]}')
            driver.close()
            driver.switch_to.window(handles[0])
        except Exception as ex:
            print(f'  API error: {ex}')
            try:
                driver.switch_to.window(driver.window_handles[0])
            except:
                pass
else:
    print('Could not extract org ID from URL')

# 5. Check the visible text of contacts section directly
print('\n=== VISIBLE CONTACTS TEXT ===')
visible_text = driver.execute_script("""
    var result = [];
    // Get all text nodes in the card area
    var card = document.querySelector('.business-card-view, .orgpage-content-view, .scroll__container');
    if (!card) return 'No card found';
    
    var walker = document.createTreeWalker(card, NodeFilter.SHOW_TEXT, null, false);
    while (walker.nextNode()) {
        var text = walker.currentNode.textContent.trim();
        if (text && text.includes('@') && text.length > 3 && text.length < 100) {
            result.push({text: text, parent: walker.currentNode.parentElement ? walker.currentNode.parentElement.className : 'unknown'});
        }
    }
    return JSON.stringify(result);
""")
print(f'Text nodes with @: {visible_text}')

driver.quit()
print('\nDone!')
