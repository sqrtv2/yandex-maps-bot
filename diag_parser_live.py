#!/usr/bin/env python3
"""Diagnostic: check what CSS selectors work on live Yandex Maps search page."""
import sys, time, json
sys.stdout.flush()

from core.browser_manager import BrowserManager
from core.playwright_driver import By

print("Starting...", flush=True)

bm = BrowserManager()
profile_data = {
    "name": "Parser-diag",
    "user_agent": None,
    "viewport_width": 1920,
    "viewport_height": 1080,
    "timezone": "Europe/Moscow",
    "language": "ru-RU",
}

bid = bm.create_browser_session(profile_data, None)
driver = bm.active_browsers[bid]

url = "https://yandex.ru/maps/?text=%D0%9C%D0%B5%D0%B1%D0%B5%D0%BB%D1%8C%D0%BD%D0%B0%D1%8F+%D1%84%D0%B0%D0%B1%D1%80%D0%B8%D0%BA%D0%B0+%D0%9C%D0%BE%D1%81%D0%BA%D0%B2%D0%B0"
print("Navigating...", flush=True)
driver.get(url)
time.sleep(12)

print("URL:", driver.current_url[:150], flush=True)
print("Title:", driver.execute_script("return document.title"), flush=True)

# Check old selectors
for sel in [".search-business-snippet-view", ".search-snippet-view"]:
    n = len(driver.find_elements(By.CSS_SELECTOR, sel))
    print(f"  OLD {sel}: {n}", flush=True)

# Check alternatives
alt_selectors = [
    'a[href*="/org/"]',
    '[class*=snippet]', '[class*=Snippet]',
    '[class*=search-list]', '[class*=SearchList]',
    '[class*=search-item]', '[class*=SearchItem]',
    '[class*=results]', '[class*=Results]',
    '[class*=business]', '[class*=Business]',
    'li[class]', '[data-id]',
]
for sel in alt_selectors:
    try:
        n = len(driver.find_elements(By.CSS_SELECTOR, sel))
        if n > 0:
            print(f"  FOUND: {sel}: {n}", flush=True)
    except Exception as e:
        print(f"  ERROR: {sel}: {e}", flush=True)

captcha = len(driver.find_elements(By.CSS_SELECTOR, "[class*=aptcha]"))
print(f"Captcha: {captcha}", flush=True)

classes = driver.execute_script("""
    let all = document.querySelectorAll('*');
    let cls = new Set();
    for (let el of all) {
        if (el.className && typeof el.className === 'string') {
            for (let c of el.className.split(' ')) {
                if (c.includes('search') || c.includes('Search') || 
                    c.includes('snippet') || c.includes('Snippet') ||
                    c.includes('result') || c.includes('Result') ||
                    c.includes('list-item') || c.includes('ListItem') ||
                    c.includes('business') || c.includes('Business') ||
                    c.includes('card') || c.includes('Card') ||
                    c.includes('org')) {
                    cls.add(c);
                }
            }
        }
    }
    return Array.from(cls).sort().join('\\n');
""")
print("--- Relevant CSS classes ---", flush=True)
print(classes[:3000], flush=True)

# Get sidebar container info
sidebar_html = driver.execute_script("""
    let containers = [
        document.querySelector('.scroll__container'),
        document.querySelector('[class*=searchResults]'),
        document.querySelector('[class*=SearchResults]'),
        document.querySelector('[class*=search-list]'),
        document.querySelector('[class*=SearchList]'),
        document.querySelector('[class*=sidebar]'),
    ];
    for (let c of containers) {
        if (c && c.children.length > 0) {
            let html = '';
            for (let i = 0; i < Math.min(3, c.children.length); i++) {
                html += '--- CHILD ' + i + ' tag=' + c.children[i].tagName + ' class=' + c.children[i].className.substring(0,200) + '\\n';
                html += c.children[i].innerHTML.substring(0, 500) + '\\n';
            }
            return 'Container: ' + c.className.substring(0,100) + '\\n' + html;
        }
    }
    return 'No container found';
""")
print("--- Sidebar HTML ---", flush=True)
print(sidebar_html[:3000], flush=True)

# Get org links
org_links = driver.execute_script("""
    let links = document.querySelectorAll('a[href*="/org/"]');
    let result = [];
    for (let i = 0; i < Math.min(5, links.length); i++) {
        result.push({
            href: links[i].href.substring(0, 200),
            text: links[i].textContent.substring(0, 100).trim(),
            parentClass: links[i].parentElement ? links[i].parentElement.className.substring(0, 150) : 'none'
        });
    }
    return JSON.stringify(result, null, 2);
""")
print("--- Org links ---", flush=True)
print(org_links, flush=True)

driver.save_screenshot("/app/screenshots/parser_diag.png")
print("Screenshot saved", flush=True)

bm.close_browser_session(bid)
print("Done", flush=True)
