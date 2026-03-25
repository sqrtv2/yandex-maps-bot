"""Debug v4: Dump full API response and check the org with email on Yandex Maps."""
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

# Step 1: Use CDP to intercept XHR requests 
driver.execute_cdp_cmd('Network.enable', {})

# Navigate to a company page
driver.get('https://yandex.ru/maps/org/gemotest/237720251425/')
time.sleep(8)

# Step 2: Check Performance log for API calls
print('=== CHECKING PERFORMANCE ENTRIES (API calls) ===')
api_entries = driver.execute_script("""
    var entries = performance.getEntriesByType('resource');
    var apiCalls = entries.filter(e => 
        e.name.includes('/api/') || 
        e.name.includes('business') || 
        e.name.includes('orgpage') ||
        e.name.includes('org/')
    );
    return apiCalls.map(e => e.name).slice(0, 30);
""")
for e in api_entries:
    print(f'  {e[:150]}')

# Step 3: Fetch the org API with full response
print('\n=== FULL API RESPONSE ===')
api_response = driver.execute_script("""
    return new Promise((resolve) => {
        fetch('/maps/api/business/fetchByIds?ids=237720251425&lang=ru_RU')
            .then(r => r.text())
            .then(text => resolve(text))
            .catch(e => resolve('error: ' + e.message));
    });
""")
time.sleep(3)
# Execute async fetch
api_response = driver.execute_async_script("""
    var callback = arguments[arguments.length - 1];
    fetch('https://yandex.ru/maps/api/business/fetchByIds?ids=237720251425&lang=ru_RU')
        .then(r => r.text())
        .then(text => callback(text))
        .catch(e => callback('error: ' + e.message));
""")

if api_response:
    print(f'Response length: {len(api_response)}')
    try:
        data = json.loads(api_response)
        # Pretty-print with focus on contact-related fields
        def extract_contacts(obj, path='', depth=0):
            if depth > 10:
                return
            if isinstance(obj, dict):
                for k, v in obj.items():
                    kl = k.lower()
                    if any(x in kl for x in ['contact', 'phone', 'email', 'mail', 'url', 'site', 'website', 'link', 'social', 'address', 'feature']):
                        print(f'  {"  "*depth}{path}.{k} = {json.dumps(v, ensure_ascii=False)[:300]}')
                    extract_contacts(v, f'{path}.{k}', depth+1)
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    extract_contacts(item, f'{path}[{i}]', depth+1)
        
        print('\nContact-related fields:')
        extract_contacts(data)
        
        # Also dump first-level keys
        if isinstance(data, dict):
            print(f'\nTop-level keys: {list(data.keys())}')
            for k, v in data.items():
                if isinstance(v, dict):
                    print(f'  {k}: {list(v.keys())[:20]}')
                elif isinstance(v, list) and v:
                    print(f'  {k}: list[{len(v)}]')
                    if isinstance(v[0], dict):
                        print(f'    [0] keys: {list(v[0].keys())[:20]}')
    except json.JSONDecodeError:
        print(f'Raw first 1000 chars: {api_response[:1000]}')
else:
    print('No response')

# Step 4: Try orgpage API with more params
print('\n=== ORGPAGE API WITH PARAMS ===')
orgpage_response = driver.execute_async_script("""
    var callback = arguments[arguments.length - 1];
    fetch('https://yandex.ru/maps/api/orgpage/fetchOrg?orgId=237720251425&lang=ru_RU&type=main')
        .then(r => r.text())
        .then(text => callback(text))
        .catch(e => callback('error: ' + e.message));
""")
if orgpage_response:
    print(f'Response length: {len(orgpage_response)}')
    try:
        data2 = json.loads(orgpage_response)
        extract_contacts(data2)
        if isinstance(data2, dict):
            print(f'\nTop-level keys: {list(data2.keys())}')
    except json.JSONDecodeError:
        print(f'Raw first 1000 chars: {orgpage_response[:1000]}')

# Step 5: Check the actual full DOM snapshot for email content
print('\n=== FULL PAGE TEXT SCAN FOR REAL EMAILS ===')
full_text = driver.execute_script("return document.body.innerText;")
# Find real email patterns (at least 3 chars before @)
real_emails = re.findall(r'[a-zA-Z0-9_.+-]{3,}@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}', full_text)
real_emails = [e for e in real_emails if not any(x in e.lower() for x in ['maps.yandex', 'yandex-team', 'support.yandex'])]
if real_emails:
    for e in real_emails:
        print(f'  Found in visible text: {e}')
        # Context
        idx = full_text.find(e)
        if idx > -1:
            print(f'    Context: {full_text[max(0,idx-80):idx+80]}')
else:
    print('  No real emails in visible page text')

# Step 6: Check if yandex.ru/maps/org/{name}/{id}/contacts/ page has more info
print('\n=== CHECKING /contacts/ PAGE ===')
driver.get('https://yandex.ru/maps/org/gemotest/237720251425/contacts/')
time.sleep(6)
contacts_text = driver.execute_script("return document.body.innerText;")
real_emails2 = re.findall(r'[a-zA-Z0-9_.+-]{3,}@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}', contacts_text)
real_emails2 = [e for e in real_emails2 if not any(x in e.lower() for x in ['maps.yandex', 'yandex-team', 'support.yandex'])]
if real_emails2:
    print(f'  Emails on contacts page: {real_emails2}')
else:
    print('  No emails on contacts page')
    # Show what IS on the contacts page
    lines = [l.strip() for l in contacts_text.split('\n') if l.strip()]
    print(f'  Text lines count: {len(lines)}')
    for l in lines[:30]:
        print(f'    {l[:120]}')

driver.quit()
print('\nDone!')
