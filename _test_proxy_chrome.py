"""Test proxy auth with Chrome/Playwright using extension approach"""
import json
import os
import subprocess
import sys
import tempfile

from rebrowser_playwright.sync_api import sync_playwright

PROXY_HOST = "185.234.59.13"
PROXY_PORT = "15056"
PROXY_USER = "GAY3Vy"
PROXY_PASS = "ud8ZAgZep5AS"

# Create proxy auth extension
ext_dir = os.path.join(tempfile.gettempdir(), "test_proxy_auth_ext")
os.makedirs(ext_dir, exist_ok=True)

manifest = {
    "version": "1.0.0",
    "manifest_version": 2,
    "name": "Proxy Auth Helper",
    "permissions": ["proxy", "webRequest", "webRequestBlocking", "<all_urls>"],
    "background": {"scripts": ["background.js"]},
    "minimum_chrome_version": "76.0.0"
}

background_js = f"""
chrome.webRequest.onAuthRequired.addListener(
    function(details) {{
        return {{
            authCredentials: {{
                username: '{PROXY_USER}',
                password: '{PROXY_PASS}'
            }}
        }};
    }},
    {{urls: ['<all_urls>']}},
    ['blocking']
);
"""

with open(os.path.join(ext_dir, "manifest.json"), "w") as f:
    json.dump(manifest, f)
with open(os.path.join(ext_dir, "background.js"), "w") as f:
    f.write(background_js)

print(f"Extension dir: {ext_dir}")

p = sync_playwright().start()
browser = p.chromium.launch_persistent_context(
    "/tmp/test_proxy_profile7",
    headless=False,
    executable_path="/opt/pw-browsers/chromium-1208/chrome-linux64/chrome",
    proxy={
        "server": f"http://{PROXY_HOST}:{PROXY_PORT}",
        # NO username/password — extension handles auth
    },
    args=[
        "--no-sandbox",
        "--disable-dev-shm-usage",
        f"--load-extension={ext_dir}",
        f"--disable-extensions-except={ext_dir}",
    ],
    ignore_default_args=["--disable-extensions", "--enable-automation"],
    timeout=30000,
)

page = browser.pages[0] if browser.pages else browser.new_page()

# Test 1: HTTP
print("\n=== TEST 1: HTTP ===")
try:
    page.goto("http://httpbin.org/ip", timeout=15000)
    print("HTTP SUCCESS:", page.content()[:200])
except Exception as e:
    print("HTTP FAILED:", str(e)[:200])

# Test 2: HTTPS
print("\n=== TEST 2: HTTPS ===")
try:
    page.goto("https://ya.ru", timeout=20000)
    print("HTTPS SUCCESS: title=", page.title())
except Exception as e:
    print("HTTPS FAILED:", str(e)[:200])

browser.close()
p.stop()
print("\nDone.")
