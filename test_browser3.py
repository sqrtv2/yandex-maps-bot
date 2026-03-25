from playwright.sync_api import sync_playwright
import time

p = sync_playwright().start()
ctx = p.chromium.launch_persistent_context(
    user_data_dir='/tmp/test_profile_death3',
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

# Block window.close
page.add_init_script("""
    window.__original_close = window.close;
    window.close = function() { 
        console.log('BLOCKED: window.close() called!'); 
    };
""")

# Listen for page close/crash events
page.on('close', lambda: print('EVENT: page closed!'))
page.on('crash', lambda: print('EVENT: page crashed!'))
ctx.on('close', lambda: print('EVENT: context closed!'))

page.goto('https://ya.ru', timeout=30000, wait_until='commit')
time.sleep(2)
print(f'ya.ru title: {page.title()}')

page.wait_for_selector('textarea[name="text"]', timeout=10000)
page.fill('textarea[name="text"]', 'test query')
page.click('button.search3__button', timeout=5000)
time.sleep(3)
print(f'After search URL: {page.url[:120]}')
print(f'After search title: {page.title()}')

# Monitor with console logs
page.on('console', lambda msg: print(f'CONSOLE: {msg.text}') if 'BLOCKED' in msg.text or 'close' in msg.text.lower() else None)

for i in range(12):
    time.sleep(5)
    try:
        val = page.evaluate('document.title')
        print(f'Alive at {(i+1)*5}s, title: {val[:50]}')
    except Exception as e:
        err = str(e)
        if 'closed' in err.lower():
            print(f'DEAD at {(i+1)*5}s (CLOSED): {err[:150]}')
        elif 'crash' in err.lower():
            print(f'DEAD at {(i+1)*5}s (CRASHED): {err[:150]}')
        elif 'destroyed' in err.lower():
            print(f'DEAD at {(i+1)*5}s (CONTEXT DESTROYED): {err[:150]}')
        else:
            print(f'DEAD at {(i+1)*5}s (OTHER): {err[:150]}')
        break

try:
    ctx.close()
except Exception:
    pass
p.stop()
print('DONE')
