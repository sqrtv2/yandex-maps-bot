#!/usr/bin/env python3
"""Test if proxy extension actually works in the browser."""
import os, sys, json, tempfile, zipfile, time

os.environ['YANDEX_BOT_BROWSER_HEADLESS'] = 'false'
sys.path.insert(0, os.path.dirname(__file__))

import undetected_chromedriver as uc
from selenium.webdriver.chrome.options import Options

# Proxy settings
PROXY = {
    'host': 'mproxy.site',
    'port': 12138,
    'username': 'Hes9yF',
    'password': 'zAU2vaEUf4TU',
    'proxy_type': 'http'
}


def create_proxy_extension(proxy_data):
    host = proxy_data['host']
    port = proxy_data['port']
    username = proxy_data['username']
    password = proxy_data['password']
    scheme = 'http'

    manifest_json = json.dumps({
        "version": "1.0.0",
        "manifest_version": 2,
        "name": "Chrome Proxy Auth",
        "permissions": [
            "proxy", "tabs", "unlimitedStorage", "storage",
            "<all_urls>", "webRequest", "webRequestBlocking"
        ],
        "background": {"scripts": ["background.js"]},
        "minimum_chrome_version": "76.0.0"
    })

    background_js = """var config = {
    mode: "fixed_servers",
    rules: {
        singleProxy: {
            scheme: "%s",
            host: "%s",
            port: parseInt(%s)
        },
        bypassList: ["localhost", "127.0.0.1"]
    }
};

chrome.proxy.settings.set({value: config, scope: "regular"}, function() {
    console.log("Proxy settings applied: %s:%s");
});

function callbackFn(details) {
    console.log("Auth requested for: " + details.url);
    return {
        authCredentials: {
            username: "%s",
            password: "%s"
        }
    };
}

chrome.webRequest.onAuthRequired.addListener(
    callbackFn,
    {urls: ["<all_urls>"]},
    ['blocking']
);
console.log("Proxy extension loaded!");
""" % (scheme, host, port, host, port, username, password)

    ext_dir = tempfile.mkdtemp(prefix='proxy_test_')
    ext_path = os.path.join(ext_dir, 'proxy_auth.zip')
    with zipfile.ZipFile(ext_path, 'w') as zf:
        zf.writestr('manifest.json', manifest_json)
        zf.writestr('background.js', background_js)
    print(f"📦 Extension created: {ext_path}")
    return ext_path


def test_with_extension():
    """Test 1: Using options.add_extension() — same as current code."""
    print("\n" + "="*60)
    print("TEST 1: add_extension() + --disable-extensions-except")
    print("="*60)
    
    ext_path = create_proxy_extension(PROXY)
    options = Options()
    options.add_extension(ext_path)
    options.add_argument("--disable-extensions-except=" + ext_path)
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-blink-features=AutomationControlled")
    
    driver = uc.Chrome(options=options, version_main=144)
    time.sleep(2)
    
    driver.get("https://yandex.ru/internet/")
    time.sleep(5)
    
    # Get page text
    body = driver.find_element("tag name", "body").text
    print(f"📄 Page body:\n{body[:500]}")
    
    # Check URL
    print(f"📍 URL: {driver.current_url}")
    
    input("\n⏎ Press Enter to close...")
    driver.quit()


def test_without_disable_flag():
    """Test 2: Using add_extension() WITHOUT --disable-extensions-except."""
    print("\n" + "="*60)
    print("TEST 2: add_extension() WITHOUT --disable-extensions-except")
    print("="*60)
    
    ext_path = create_proxy_extension(PROXY)
    options = Options()
    options.add_extension(ext_path)
    # NO --disable-extensions-except!
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-blink-features=AutomationControlled")
    
    driver = uc.Chrome(options=options, version_main=144)
    time.sleep(2)
    
    driver.get("https://yandex.ru/internet/")
    time.sleep(5)
    
    body = driver.find_element("tag name", "body").text
    print(f"📄 Page body:\n{body[:500]}")
    print(f"📍 URL: {driver.current_url}")
    
    input("\n⏎ Press Enter to close...")
    driver.quit()


def test_with_proxy_arg():
    """Test 3: Using --proxy-server arg (no auth, for comparison)."""
    print("\n" + "="*60)
    print("TEST 3: --proxy-server argument (no extension)")
    print("="*60)
    
    options = Options()
    options.add_argument(f"--proxy-server=http://{PROXY['host']}:{PROXY['port']}")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-blink-features=AutomationControlled")
    
    driver = uc.Chrome(options=options, version_main=144)
    time.sleep(2)
    
    # This will likely fail auth, but let's see
    driver.get("https://yandex.ru/internet/")
    time.sleep(5)
    
    body = driver.find_element("tag name", "body").text
    print(f"📄 Page body:\n{body[:500]}")
    print(f"📍 URL: {driver.current_url}")
    
    input("\n⏎ Press Enter to close...")
    driver.quit()


def test_with_load_extension():
    """Test 4: Using --load-extension with unpacked directory."""
    print("\n" + "="*60)
    print("TEST 4: --load-extension with UNPACKED directory")
    print("="*60)
    
    proxy = PROXY
    ext_dir = tempfile.mkdtemp(prefix='proxy_unpacked_')
    
    manifest = {
        "version": "1.0.0",
        "manifest_version": 2,
        "name": "Chrome Proxy Auth",
        "permissions": [
            "proxy", "tabs", "unlimitedStorage", "storage",
            "<all_urls>", "webRequest", "webRequestBlocking"
        ],
        "background": {"scripts": ["background.js"]},
        "minimum_chrome_version": "76.0.0"
    }
    
    background_js = """var config = {
    mode: "fixed_servers",
    rules: {
        singleProxy: {
            scheme: "http",
            host: "%s",
            port: parseInt(%s)
        },
        bypassList: ["localhost", "127.0.0.1"]
    }
};
chrome.proxy.settings.set({value: config, scope: "regular"}, function() {});
function callbackFn(details) {
    return { authCredentials: { username: "%s", password: "%s" } };
}
chrome.webRequest.onAuthRequired.addListener(callbackFn, {urls: ["<all_urls>"]}, ['blocking']);
""" % (proxy['host'], proxy['port'], proxy['username'], proxy['password'])
    
    with open(os.path.join(ext_dir, 'manifest.json'), 'w') as f:
        json.dump(manifest, f)
    with open(os.path.join(ext_dir, 'background.js'), 'w') as f:
        f.write(background_js)
    
    print(f"📁 Unpacked extension: {ext_dir}")
    
    options = Options()
    options.add_argument(f"--load-extension={ext_dir}")
    options.add_argument(f"--disable-extensions-except={ext_dir}")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-blink-features=AutomationControlled")
    
    driver = uc.Chrome(options=options, version_main=144)
    time.sleep(2)
    
    driver.get("https://yandex.ru/internet/")
    time.sleep(5)
    
    body = driver.find_element("tag name", "body").text
    print(f"📄 Page body:\n{body[:500]}")
    print(f"📍 URL: {driver.current_url}")
    
    input("\n⏎ Press Enter to close...")
    driver.quit()


if __name__ == "__main__":
    print("Choose test:")
    print("1: add_extension + --disable-extensions-except (CURRENT CODE)")
    print("2: add_extension WITHOUT --disable-extensions-except")
    print("3: --proxy-server arg (no auth)")
    print("4: --load-extension with unpacked directory")
    
    choice = input("Enter 1-4: ").strip()
    
    if choice == "1":
        test_with_extension()
    elif choice == "2":
        test_without_disable_flag()
    elif choice == "3":
        test_with_proxy_arg()
    elif choice == "4":
        test_with_load_extension()
    else:
        print("Running test 2 (most likely fix)...")
        test_without_disable_flag()
