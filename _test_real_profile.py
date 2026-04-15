"""Open a real profile with proxy and navigate to a site - standalone test."""
import os
import sys
import json
import time
import logging
import tempfile

logging.basicConfig(level=logging.INFO, format='%(message)s')

from rebrowser_playwright.sync_api import sync_playwright
from app.database import SessionLocal
from app.models import BrowserProfile, ProxyServer
from app.config import settings

db = SessionLocal()

profile = db.query(BrowserProfile).filter(BrowserProfile.status != 'deleted').first()
print(f'Profile: {profile.name}, ID={profile.id}')

proxy = db.query(ProxyServer).filter(ProxyServer.id == 14).first()
print(f'Proxy: {proxy.host}:{proxy.port} user={proxy.username} pass={proxy.password}')

profile_dir = os.path.join(settings.browser_user_data_dir, profile.name)

# Remove stale lock
for lockfile in ['SingletonLock', 'SingletonSocket', 'SingletonCookie']:
    lp = os.path.join(profile_dir, lockfile)
    if os.path.exists(lp) or os.path.islink(lp):
        os.remove(lp)

import glob
chromium_paths = sorted(glob.glob('/opt/pw-browsers/chromium-*/chrome-linux*/chrome'))
chrome_exe = chromium_paths[-1] if chromium_paths else None
print(f'Chrome: {chrome_exe}')

# Chrome net log for debugging
net_log = '/tmp/chrome_net.log'

pw = sync_playwright().start()

# Test 1: WITHOUT proxy auth (just proxy server, no user/pass)
print('\n=== TEST 1: No proxy (direct) ===')
try:
    ctx1 = pw.chromium.launch_persistent_context(
        user_data_dir='/tmp/test_noproxy',
        headless=False,
        executable_path=chrome_exe,
        args=['--no-sandbox', '--disable-dev-shm-usage'],
        viewport={'width': 1366, 'height': 768},
        ignore_https_errors=True,
        timeout=30000,
    )
    page1 = ctx1.pages[0] if ctx1.pages else ctx1.new_page()
    page1.goto('https://httpbin.org/ip', timeout=15000)
    print(f'Direct OK: {page1.content()[:150]}')
    ctx1.close()
except Exception as e:
    print(f'Direct FAILED: {e}')
    try:
        ctx1.close()
    except:
        pass

# Test 2: HTTP through proxy (no CONNECT tunnel)
print('\n=== TEST 2: HTTP through proxy ===')
try:
    ctx2 = pw.chromium.launch_persistent_context(
        user_data_dir='/tmp/test_httpproxy',
        headless=False,
        executable_path=chrome_exe,
        proxy={
            'server': f'http://{proxy.host}:{proxy.port}',
            'username': proxy.username,
            'password': proxy.password,
        },
        args=['--no-sandbox', '--disable-dev-shm-usage'],
        viewport={'width': 1366, 'height': 768},
        ignore_https_errors=True,
        timeout=30000,
    )
    page2 = ctx2.pages[0] if ctx2.pages else ctx2.new_page()
    page2.goto('http://httpbin.org/ip', timeout=15000)
    print(f'HTTP proxy OK: {page2.content()[:150]}')
    ctx2.close()
except Exception as e:
    print(f'HTTP proxy FAILED: {e}')
    try:
        ctx2.close()
    except:
        pass

# Test 3: HTTPS through proxy (CONNECT tunnel + auth)  
print('\n=== TEST 3: HTTPS through proxy ===')
try:
    ctx3 = pw.chromium.launch_persistent_context(
        user_data_dir='/tmp/test_httpsproxy',
        headless=False,
        executable_path=chrome_exe,
        proxy={
            'server': f'http://{proxy.host}:{proxy.port}',
            'username': proxy.username,
            'password': proxy.password,
        },
        args=['--no-sandbox', '--disable-dev-shm-usage', f'--log-net-log={net_log}', '--net-log-capture-mode=Everything'],
        viewport={'width': 1366, 'height': 768},
        ignore_https_errors=True,
        timeout=30000,
    )
    page3 = ctx3.pages[0] if ctx3.pages else ctx3.new_page()
    page3.goto('https://httpbin.org/ip', timeout=15000)
    print(f'HTTPS proxy OK: {page3.content()[:150]}')
    ctx3.close()
except Exception as e:
    print(f'HTTPS proxy FAILED: {e}')
    try:
        ctx3.close()
    except:
        pass

# Check net log for clues
if os.path.exists(net_log):
    with open(net_log, 'r') as f:
        data = f.read()
    # Search for tunnel/auth related entries
    for keyword in ['ERR_TUNNEL', '407', 'AUTH_NEEDED', 'PROXY_AUTH', 'HttpProxyConnectJob']:
        count = data.count(keyword)
        if count:
            print(f'Net log: {keyword} appears {count} times')
    # Find the error
    import re
    errors = re.findall(r'"params":\{[^}]*"net_error":[^}]*\}', data)
    for err in errors[:5]:
        print(f'Net error: {err}')

pw.stop()
print('\nDone')
db.close()
