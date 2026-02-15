#!/usr/bin/env python3
"""
Локальный тест визита — запускает браузер ВИДИМО (не headless),
с прогретым профилем и куками, открывает целевой URL.
Визит выполняется напрямую, без Celery.

Использование:
    python3 test_visit_local.py
"""
import os
import sys
import time
import random
import logging

# Принудительно headless=false для визуального теста
os.environ['YANDEX_BOT_BROWSER_HEADLESS'] = 'false'
os.environ['YANDEX_BOT_DEBUG'] = 'true'
os.environ['YANDEX_BOT_DATABASE_URL'] = 'sqlite:///./yandex_maps_bot.db'

# Настраиваем логирование чтобы всё было видно в консоли
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# Импорты приложения
from app.config import settings
from app.database import get_db_session
from app.models import BrowserProfile
from core.browser_manager import BrowserManager
from core.proxy_manager import ProxyManager
from core.captcha_solver import CaptchaSolver
from core.profile_generator import ProfileGenerator
from tasks.yandex_maps import (
    detect_captcha_or_block, handle_yandex_protection,
    perform_yandex_visit_actions, perform_passive_browsing
)

TARGET_URL = "https://yandex.ru/maps/org/benesque/193289471730/"
PROFILE_ID = 1  # Profile-1 (warmed)


def main():
    logger.info(f"=== ЛОКАЛЬНЫЙ ТЕСТ ВИЗИТА ===")
    logger.info(f"headless = {settings.browser_headless}")
    logger.info(f"Target URL: {TARGET_URL}")
    logger.info(f"Profile ID: {PROFILE_ID}")
    logger.info(f"Browser profiles dir: {os.path.abspath(settings.browser_user_data_dir)}")
    
    # 1. Получаем профиль из БД
    with get_db_session() as db:
        profile_obj = db.query(BrowserProfile).filter(BrowserProfile.id == PROFILE_ID).first()
        if not profile_obj:
            logger.error(f"Profile {PROFILE_ID} not found!")
            sys.exit(1)
        
        profile_data_from_db = {
            'name': profile_obj.name,
            'user_agent': profile_obj.user_agent,
            'viewport_width': profile_obj.viewport_width,
            'viewport_height': profile_obj.viewport_height,
            'timezone': profile_obj.timezone,
            'language': profile_obj.language,
            'proxy_host': profile_obj.proxy_host,
            'proxy_port': profile_obj.proxy_port,
            'proxy_username': profile_obj.proxy_username,
            'proxy_password': profile_obj.proxy_password,
            'proxy_type': profile_obj.proxy_type,
        }
        logger.info(f"Profile: {profile_data_from_db['name']}, UA: {profile_data_from_db['user_agent'][:60]}...")
        logger.info(f"Profile dir exists: {os.path.exists(os.path.join(settings.browser_user_data_dir, profile_data_from_db['name']))}")
        
        # Проверяем cookies
        cookies_file = os.path.join(settings.browser_user_data_dir, profile_data_from_db['name'], 'Default', 'Cookies')
        if os.path.exists(cookies_file):
            size = os.path.getsize(cookies_file)
            logger.info(f"✅ Cookies file: {cookies_file} ({size} bytes)")
        else:
            logger.warning(f"⚠️ No cookies file found at {cookies_file}")

    # 2. Настраиваем прокси
    proxy_data = None
    if profile_data_from_db['proxy_host'] and profile_data_from_db['proxy_port']:
        proxy_data = {
            'host': profile_data_from_db['proxy_host'],
            'port': profile_data_from_db['proxy_port'],
            'username': profile_data_from_db['proxy_username'],
            'password': profile_data_from_db['proxy_password'],
            'proxy_type': profile_data_from_db['proxy_type'] or 'http'
        }
    else:
        # Используем прокси из менеджера
        proxy_manager = ProxyManager()
        proxy_manager.load_proxies_from_db()
        proxy_data = proxy_manager.get_available_proxy()
    
    if proxy_data:
        logger.info(f"📡 Proxy: {proxy_data.get('proxy_type', 'http')}://{proxy_data['host']}:{proxy_data['port']} (user={proxy_data.get('username', 'none')})")
    else:
        logger.warning("⚠️ NO PROXY — visit will go from your real IP")

    # 3. Создаём профиль
    profile_generator = ProfileGenerator()
    profile_data = profile_generator.generate_profile(profile_data_from_db['name'])
    profile_data.update({
        'user_agent': profile_data_from_db['user_agent'],
        'viewport': {
            'width': profile_data_from_db['viewport_width'],
            'height': profile_data_from_db['viewport_height']
        },
        'timezone': profile_data_from_db['timezone'],
        'language': 'ru-RU'
    })

    # 4. Запускаем браузер
    browser_manager = BrowserManager()
    browser_id = None
    
    try:
        logger.info("🚀 Запускаем браузер (ВИДИМЫЙ РЕЖИМ)...")
        browser_id = browser_manager.create_browser_session(profile_data, proxy_data)
        driver = browser_manager.active_browsers[browser_id]
        
        logger.info(f"✅ Браузер запущен. Browser ID: {browser_id}")
        
        # 5. Проверяем куки до навигации
        cookies_before = driver.get_cookies()
        logger.info(f"🍪 Cookies loaded from profile: {len(cookies_before)} cookies")
        for c in cookies_before[:5]:
            logger.info(f"   Cookie: {c.get('domain', '?')} / {c.get('name', '?')}")
        
        # 6. Открываем целевую страницу
        logger.info(f"🌐 Navigating to: {TARGET_URL}")
        if not browser_manager.navigate_to_url(browser_id, TARGET_URL, timeout=90):
            logger.error("❌ Navigation failed!")
        else:
            actual_url = driver.current_url
            logger.info(f"✅ Page loaded. Actual URL: {actual_url}")
            
            # 7. Проверяем капчу
            if detect_captcha_or_block(driver):
                logger.warning("⚠️ CAPTCHA detected!")
                captcha_solver = CaptchaSolver()
                if handle_yandex_protection(driver, captcha_solver):
                    logger.info("✅ Captcha solved!")
                else:
                    logger.error("❌ Could not solve captcha")
            else:
                logger.info("✅ No captcha — page loaded normally")
            
            # 8. Проверяем куки после навигации
            cookies_after = driver.get_cookies()
            logger.info(f"🍪 Cookies after navigation: {len(cookies_after)} cookies")
            yandex_cookies = [c for c in cookies_after if 'yandex' in c.get('domain', '')]
            logger.info(f"🍪 Yandex cookies: {len(yandex_cookies)}")
            for c in yandex_cookies[:10]:
                logger.info(f"   {c['domain']} / {c['name']} = {str(c.get('value', ''))[:50]}")
            
            # 9. Проверяем что страница реально загрузилась (page title, body text)
            try:
                title = driver.title
                logger.info(f"📄 Page title: {title}")
            except:
                pass
            
            # 10. Делаем скриншот
            ss_path = f"screenshots/test_visit_{int(time.time())}.png"
            driver.save_screenshot(ss_path)
            logger.info(f"📸 Screenshot saved: {ss_path}")
            
            # 11. Ждём для визуальной проверки
            logger.info("")
            logger.info("=" * 60)
            logger.info("👁️  БРАУЗЕР ОТКРЫТ — СМОТРИТЕ ЧТО ПРОИСХОДИТ")
            logger.info("=" * 60)
            logger.info("Нажмите Enter чтобы выполнить действия на странице...")
            logger.info("(или Ctrl+C чтобы закрыть)")
            
            try:
                input()
            except KeyboardInterrupt:
                logger.info("Закрываем...")
                return
            
            # 12. Выполняем действия на странице
            logger.info("🎯 Выполняем действия на странице...")
            visit_params = {
                'min_visit_time': 10,
                'max_visit_time': 20,
                'actions': ['scroll', 'view_photos', 'read_reviews', 'click_contacts', 'view_map'],
                'scroll_probability': 0.9,
                'photo_click_probability': 0.7,
                'review_read_probability': 0.8,
                'contact_click_probability': 0.5,
                'map_interaction_probability': 0.6
            }
            
            visit_results = perform_yandex_visit_actions(browser_manager, browser_id, visit_params)
            logger.info(f"📊 Visit results: {visit_results}")
            
            # 13. Финальный скриншот
            ss_path2 = f"screenshots/test_visit_after_{int(time.time())}.png"
            driver.save_screenshot(ss_path2)
            logger.info(f"📸 Final screenshot: {ss_path2}")
            
            # 14. Ждём для проверки
            logger.info("")
            logger.info("=" * 60)
            logger.info("👁️  ДЕЙСТВИЯ ВЫПОЛНЕНЫ — ПРОВЕРЬТЕ РЕЗУЛЬТАТ")
            logger.info("=" * 60)
            logger.info("Нажмите Enter чтобы закрыть браузер...")
            
            try:
                input()
            except KeyboardInterrupt:
                pass
    
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        if browser_manager and browser_id:
            logger.info("🧹 Закрываем браузер...")
            browser_manager.close_browser_session(browser_id)
        logger.info("Done.")


if __name__ == '__main__':
    main()
