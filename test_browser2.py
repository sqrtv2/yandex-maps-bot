from playwright.sync_api import sync_playwright
import time

p = sync_playwright().start()
ctx = p.chromium.launch_persistent_context(
    user_data_dir='/tmp/test_profile_death2',
    headless=True,
    args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu',
          '--disable-software-rasterizer', '--js-flags=--max-old-space-size=512',
          '--disable-features=TranslateUI,BlinkGenPropertyTrees,IsolateOrigins,site-per-process'],
    proxy={'server': 'http://95.31.170.9:4097', 'username': 'sqrtv2', 'password': '21607141'},
    viewport={'width': 1366, 'height': 768},
    locale='ru-RU',
    ignore_https_errors=True,
)
page = ctx.pages[0] if ctx.pages else ctx.new_page()
print('Browser started OK')

try:
    page.goto('https://mail.ru', timeout=30000, wait_until='commit')
    time.sleep(3)
    try:
        t = page.title()
        print(f'mail.ru title: {t}')
    except Exception as e:
        print(f'mail.ru title error: {e}')
        time.sleep(2)
        try:
            t = page.title()
            print(f'mail.ru title (retry): {t}')
        except Exception as e2:
            print(f'mail.ru title retry error: {e2}')
except Exception as e:
    print(f'mail.ru goto error: {e}')

try:
    page.evaluate('1')
    print('Page alive after mail.ru')
except Exception as e:
    print(f'Page DEAD after mail.ru: {e}')

try:
    page.goto('https://ya.ru', timeout=30000, wait_until='commit')
    time.sleep(2)
    t = page.title()
    print(f'ya.ru title: {t}')
except Exception as e:
    print(f'ya.ru error: {e}')

try:
    page.evaluate('1')
    print('Page alive after ya.ru')
except Exception as e:
    print(f'Page DEAD after ya.ru: {e}')

try:
    page.wait_for_selector('textarea[name="text"]', timeout=10000)
    page.fill('textarea[name="text"]', 'test query')
    print('Typed search query')
    time.sleep(1)
    page.click('button.search3__button', timeout=5000)
    time.sleep(3)
    print(f'After search URL: {page.url[:120]}')
    print(f'After search title: {page.title()}')
except Exception as e:
    print(f'Search error: {e}')

for i in range(6):
    time.sleep(5)
    try:
        page.evaluate('1')
        print(f'Page alive at {(i+1)*5}s')
    except Exception as e:
        print(f'Page DEAD at {(i+1)*5}s: {e}')
        break

ctx.close()
p.stop()
print('DONE')
