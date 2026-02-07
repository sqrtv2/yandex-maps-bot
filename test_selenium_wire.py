"""Простой тест selenium-wire с прокси"""
import sys
sys.path.insert(0, '/Users/sqrtv2/Project/PF')

from seleniumwire import webdriver
from selenium.webdriver.chrome.options import Options

print("="*70)
print("🔍 ТЕСТ SELENIUM-WIRE С ПРОКСИ")
print("="*70)

# Настройка прокси
proxy_url = 'http://Hes9yF:zAU2vaEUf4TU@mproxy.site:12138'

seleniumwire_options = {
    'proxy': {
        'http': proxy_url,
        'https': proxy_url,
        'no_proxy': 'localhost,127.0.0.1'
    }
}

print(f"Прокси: {proxy_url}")
print("")

# Chrome опции
options = Options()
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')

print("Создаём браузер...")
driver = webdriver.Chrome(
    options=options,
    seleniumwire_options=seleniumwire_options
)

print("✅ Браузер создан!")
print("Открываем api.ipify.org...")

driver.get('https://api.ipify.org')

import time
time.sleep(3)

ip_text = driver.find_element('tag name', 'body').text
print(f"\n🌍 IP через прокси: {ip_text}")

if '213.87' in ip_text or '185.234' in ip_text:
    print("✅ ПРОКСИ РАБОТАЕТ!")
else:
    print(f"⚠️ IP: {ip_text}")

print("\nОткрываем Яндекс Карты...")
driver.get('https://yandex.ru/maps/org/medsemya/1201821372')

time.sleep(5)

screenshot_path = '/Users/sqrtv2/Project/PF/screenshots/selenium_wire_test.png'
driver.save_screenshot(screenshot_path)
print(f"📸 Скриншот: {screenshot_path}")

driver.quit()

print("\n" + "="*70)
print("✅ ТЕСТ ЗАВЕРШЁН")
print("="*70)
