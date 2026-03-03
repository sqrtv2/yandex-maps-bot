#!/usr/bin/env python3
"""Quick test: Chrome + proxy + clean profile → ya.ru"""
import undetected_chromedriver as uc
import time, os, shutil, sys, logging

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])

sys.path.insert(0, os.path.dirname(__file__))
from core.browser_manager import _LocalProxyForwarder

profile_dir = os.path.abspath('./browser_profiles/Profile-Test-Debug')
if os.path.exists(profile_dir):
    shutil.rmtree(profile_dir)

# Start proxy forwarder
forwarder = _LocalProxyForwarder(
    remote_host='mproxy.site',
    remote_port=12138,
    username='Hes9yF',
    password='zAU2vaEUf4TU',
    proxy_type='http'
)
local_port = forwarder.start()
print(f'Proxy forwarder on 127.0.0.1:{local_port}')

options = uc.ChromeOptions()
options.add_argument('--no-sandbox')
options.add_argument('--window-size=1366,768')
options.add_argument('--disable-dev-shm-usage')
options.add_argument('--lang=ru-RU')
options.add_argument('--accept-lang=ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7')
options.add_argument(f'--proxy-server=http://127.0.0.1:{local_port}')

driver = uc.Chrome(
    options=options,
    user_data_dir=profile_dir,
    version_main=144
)
print('Chrome launched with proxy')

try:
    driver.get('https://ya.ru')
    time.sleep(5)
    print(f'Title: {driver.title}')
    print(f'URL: {driver.current_url}')
    driver.save_screenshot('screenshots/test_with_proxy.png')
    print('Screenshot saved: screenshots/test_with_proxy.png')

    # Detect captcha
    url_lower = driver.current_url.lower()
    src_lower = driver.page_source[:3000].lower()
    captcha_signs = []
    if 'showcaptcha' in url_lower: captcha_signs.append('showcaptcha_url')
    if 'checkboxcaptcha' in src_lower: captcha_signs.append('checkbox')
    if 'advancedcaptcha' in src_lower: captcha_signs.append('advanced')
    if 'smartcaptcha' in src_lower: captcha_signs.append('smartcaptcha')
    if 'kaleidoscope' in src_lower: captcha_signs.append('kaleidoscope')
    if 'silhouette' in src_lower: captcha_signs.append('silhouette')
    if 'я не робот' in src_lower: captcha_signs.append('ya_ne_robot')

    if captcha_signs:
        print(f'🚨 CAPTCHA DETECTED: {captcha_signs}')
        with open('screenshots/test_proxy_captcha.html', 'w', encoding='utf-8') as f:
            f.write(driver.page_source)
        print('HTML saved: screenshots/test_proxy_captcha.html')
    else:
        print('✅ No captcha detected!')

    input('\n⏸️  Press Enter to close browser...')

except Exception as e:
    print(f'Error: {e}')
    import traceback
    traceback.print_exc()
    input('\n⏸️  Press Enter to close browser...')
finally:
    driver.quit()
    forwarder.stop()
    print('Done!')
