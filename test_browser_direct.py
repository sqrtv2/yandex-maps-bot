"""Прямой тест браузера с прокси - БЕЗ Celery"""
import sys
sys.path.insert(0, '/Users/sqrtv2/Project/PF')

from core.browser_manager import BrowserManager
import logging
import time

# Настраиваем логирование
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

print("="*70)
print("🔍 ПРЯМОЙ ТЕСТ БРАУЗЕРА С ПРОКСИ")
print("="*70)
print("")

# Данные профиля
profile_data = {
    'name': 'Profile-1',
    'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'viewport': {'width': 1366, 'height': 768},
    'timezone': 'Europe/Moscow',
    'language': 'ru-RU'
}

# Данные прокси - HTTP с авторизацией
proxy_data = {
    'host': 'mproxy.site',
    'port': 12138,
    'username': 'Hes9yF',
    'password': 'zAU2vaEUf4TU',
    'proxy_type': 'http'
}

print("📋 Конфигурация:")
print(f"   Profile: {profile_data['name']}")
print(f"   Proxy: {proxy_data['proxy_type']}://{proxy_data['host']}:{proxy_data['port']}")
print(f"   Auth: {proxy_data['username']}:***")
print("")
print("="*70)
print("")

try:
    logger.info("Создаём BrowserManager...")
    manager = BrowserManager()
    
    logger.info("Создаём браузер с прокси...")
    browser_id = manager.create_browser_session(profile_data, proxy_data)
    
    logger.info(f"✅ Браузер создан: {browser_id}")
    logger.info("Ждём 5 секунд...")
    time.sleep(5)
    
    logger.info("Открываем Google для проверки прокси...")
    driver = manager.active_browsers[browser_id]
    driver.get('https://api.ipify.org')
    
    time.sleep(3)
    
    # Сохраняем скриншот
    screenshot_path = '/Users/sqrtv2/Project/PF/screenshots/proxy_test.png'
    driver.save_screenshot(screenshot_path)
    logger.info(f"📸 Скриншот сохранён: {screenshot_path}")
    
    # Пытаемся получить IP из body
    try:
        ip_text = driver.find_element('tag name', 'body').text
        logger.info(f"🌍 Текущий IP: {ip_text}")
        
        if '213.87' in ip_text or '185.234' in ip_text:
            logger.info("✅ ПРОКСИ РАБОТАЕТ!")
        else:
            logger.warning(f"⚠️ Возможно прокси не работает, IP: {ip_text}")
    except Exception as e:
        logger.error(f"Не удалось получить IP: {e}")
        logger.info("Смотрите скриншот для деталей")
    
    logger.info("Открываем Яндекс Карты...")
    driver.get('https://yandex.ru/maps/org/medsemya/1201821372')
    
    logger.info("Ждём 10 секунд для просмотра...")
    time.sleep(10)
    
    logger.info("Закрываем браузер...")
    manager.close_browser_session(browser_id)
    
    print("")
    print("="*70)
    print("✅ ТЕСТ ЗАВЕРШЁН")
    print("="*70)
    
except Exception as e:
    logger.error(f"❌ Ошибка: {e}")
    import traceback
    traceback.print_exc()
