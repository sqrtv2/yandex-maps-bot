"""Открываем браузер с Яндекс Картами - ВИДИМЫЙ режим"""
import sys
sys.path.insert(0, '/Users/sqrtv2/Project/PF')

from core.browser_manager import BrowserManager
from app.database import get_db
from app.models import BrowserProfile
import time
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

print("="*80)
print("🔍 ОТКРЫВАЕМ ЯНДЕКС КАРТЫ - ВИДИМЫЙ БРАУЗЕР")
print("="*80)
print("")

# Получаем данные профиля из БД
db = next(get_db())
profile_obj = db.query(BrowserProfile).filter_by(name='Profile-1').first()

if not profile_obj:
    print("❌ Profile-1 не найден")
    sys.exit(1)

# Формируем данные профиля
profile_data = {
    'name': profile_obj.name,
    'user_agent': profile_obj.user_agent,
    'viewport': {
        'width': profile_obj.viewport_width,
        'height': profile_obj.viewport_height
    },
    'timezone': profile_obj.timezone,
    'language': profile_obj.language
}

# Данные прокси
proxy_data = {
    'host': profile_obj.proxy_host,
    'port': profile_obj.proxy_port,
    'username': profile_obj.proxy_username,
    'password': profile_obj.proxy_password,
    'proxy_type': profile_obj.proxy_type or 'http'
}

print(f"📋 Профиль: {profile_data['name']}")
print(f"📡 Прокси: {proxy_data['proxy_type']}://{proxy_data['host']}:{proxy_data['port']}")
print(f"🔐 Авторизация: {proxy_data['username']}:***")
print("")
print("="*80)
print("")

try:
    logger.info("Создаём BrowserManager...")
    manager = BrowserManager()
    
    logger.info("Создаём браузер с прокси (selenium-wire)...")
    browser_id = manager.create_browser_session(profile_data, proxy_data)
    
    logger.info(f"✅ Браузер создан: {browser_id}")
    
    driver = manager.active_browsers[browser_id]
    
    logger.info("Открываем Яндекс Карты для Медсемья...")
    driver.get('https://yandex.ru/maps/org/medsemya/1201821372')
    
    logger.info("Ждём 5 секунд для загрузки страницы...")
    time.sleep(5)
    
    # Сохраняем скриншот
    screenshot_path = '/Users/sqrtv2/Project/PF/screenshots/yandex_maps_view.png'
    driver.save_screenshot(screenshot_path)
    logger.info(f"📸 Скриншот сохранён: {screenshot_path}")
    
    # Проверяем title
    title = driver.title
    logger.info(f"📄 Title страницы: {title}")
    
    # Ищем признаки блокировки
    page_source = driver.page_source.lower()
    
    if 'smartcaptcha' in page_source or 'captcha' in page_source:
        logger.warning("⚠️ Обнаружена КАПЧА!")
    
    if 'robot' in page_source or 'подозрительн' in page_source:
        logger.warning("⚠️ Обнаружено сообщение о подозрительной активности!")
        
    if 'доступ ограничен' in page_source or 'access denied' in page_source:
        logger.warning("⚠️ Доступ ограничен!")
    
    print("")
    print("="*80)
    print("⏸️  БРАУЗЕР ОТКРЫТ - ПОСМОТРИТЕ ЧТО ПОКАЗЫВАЕТ ЯНДЕКС")
    print("="*80)
    print("")
    print("Нажмите Enter чтобы закрыть браузер...")
    input()
    
    logger.info("Закрываем браузер...")
    manager.close_browser_session(browser_id)
    
    print("")
    print("="*80)
    print("✅ ГОТОВО")
    print("="*80)
    
except Exception as e:
    logger.error(f"❌ Ошибка: {e}")
    import traceback
    traceback.print_exc()
