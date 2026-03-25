from playwright.sync_api import sync_playwright
import time, random

p = sync_playwright().start()
profile = f'/tmp/test_clean_{random.randint(10000,99999)}'
ctx = p.chromium.launch_persistent_context(
    user_data_dir=profile,
    headless=True,
    args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu',
          '--disable-software-rasterizer', '--js-flags=--max-old-space-size=512',
          '--disable-features=TranslateUI,BlinkGenPropertyTrees,IsolateOrigins,site-per-process'],
    proxy={'server': 'http://95.31.178.33:4054', 'username': 'sqrtv2', 'password': '21607141'},
    viewport={'width': 1366, 'height': 768},
    locale='ru-RU',
    ignore_https_errors=True,
)
page = ctx.pages[0] if ctx.pages else ctx.new_page()
print(f'Browser started OK, profile: {profile}')

# Listen for close/crash
page.on('close', lambda: print('>>> EVENT: page closed!'))
page.on('crash', lambda: print('>>> EVENT: page crashed!'))

page.goto('https://ya.ru', timeout=30000, wait_until='domcontentloaded')
time.sleep(2)
title = page.title()
print(f'ya.ru title: {title}')

if 'робот' in title.lower() or 'captcha' in title.lower():
    print('CAPTCHA on ya.ru! Monitoring...')
    for i in range(12):
        time.sleep(5)
        try:
            t = page.evaluate('document.title')
            print(f'  Alive at {(i+1)*5}s, title: {t[:50]}')
        except Exception as e:
            print(f'  DEAD at {(i+1)*5}s: {str(e)[:100]}')
            break
else:
    print('No captcha, proceeding to search...')
    try:
        page.wait_for_selector('textarea[name="text"]', timeout=10000)
        page.fill('textarea[name="text"]', 'test query')
        page.click('button.search3__button', timeout=5000)
        time.sleep(3)
        title2 = page.title()
        print(f'After search: {title2[:80]}')
        if 'робот' in title2.lower():
            print('CAPTCHA after search! Monitoring...')
            for i in range(12):
                time.sleep(5)
                try:
                    t = page.evaluate('document.title')
                    print(f'  Alive at {(i+1)*5}s, title: {t[:50]}')
                except Exception as e:
                    print(f'  DEAD at {(i+1)*5}s: {str(e)[:100]}')
                    break
        else:
            for i in range(6):
                time.sleep(5)
                try:
                    page.evaluate('1')
                    print(f'  Alive at {(i+1)*5}s')
                except Exception as e:
                    print(f'  DEAD at {(i+1)*5}s: {str(e)[:100]}')
                    break
    except Exception as e:
        print(f'Search error: {e}')

try:
    ctx.close()
except Exception:
    pass
p.stop()
print('DONE')
