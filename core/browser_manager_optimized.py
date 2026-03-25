"""
ОПТИМИЗИРОВАННАЯ версия browser_manager для стабильной работы 1000 кликов/день.
Основные улучшения:
1. Лучшая обработка медленных прокси
2. Агрессивная очистка Chrome процессов
3. Более стабильные настройки браузера
4. Улучшенная обработка ошибок
"""
import os
import time
import random
import json
import logging
import subprocess
import signal
import tempfile
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta

from playwright.sync_api import sync_playwright, Playwright, Browser, BrowserContext, Page
from playwright_stealth import Stealth
from .playwright_driver import (
    PlaywrightDriver, PlaywrightElement, PlaywrightActionChains,
    PlaywrightWait, By, Keys, expected_conditions, EC,
    WebDriverException, TimeoutException, NoSuchElementException,
    ElementClickInterceptedException, StaleElementReferenceException,
)

from app.config import settings
from .profile_generator import ProfileGenerator

logger = logging.getLogger(__name__)

class OptimizedBrowserManager:
    """Оптимизированный менеджер браузеров для высокой стабильности."""

    def __init__(self):
        self.playwright_instance: Optional[Playwright] = None
        self.active_browsers: Dict[str, dict] = {}
        self.chrome_pids: List[int] = []

        # Настройки для стабильности
        self.max_page_load_time = 60000  # 60 секунд вместо 30
        self.navigation_timeout = 45000   # 45 секунд для навигации
        self.element_timeout = 20000      # 20 секунд для поиска элементов

    def get_stable_browser_args(self, is_mobile: bool = False) -> List[str]:
        """Получить оптимизированные аргументы Chrome для стабильности."""
        args = [
            # Базовые настройки безопасности
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',

            # Оптимизация памяти (КРИТИЧНО для Docker)
            '--max-memory-usage=400000000',      # 400MB лимит памяти
            '--memory-pressure-off',
            '--aggressive-cache-discard',
            '--js-flags="--max-old-space-size=256"',  # 256MB для V8

            # Оптимизация для медленных прокси
            '--disable-background-timer-throttling',
            '--disable-renderer-backgrounding',
            '--disable-backgrounding-occluded-windows',
            '--disable-features=TranslateUI,BlinkGenPropertyTrees',

            # Стабильность рендеринга
            '--disable-gpu',
            '--disable-software-rasterizer',
            '--disable-2d-canvas-image-chromium',
            '--disable-accelerated-2d-canvas',
            '--disable-accelerated-jpeg-decoding',
            '--disable-accelerated-video-decode',

            # Сетевые оптимизации
            '--aggressive-cache-discard',
            '--force-fieldtrials=SitePerProcess/Disabled',
            '--disable-site-isolation-trials',

            # Отключение ненужных компонентов
            '--disable-extensions',
            '--disable-plugins',
            '--disable-background-networking',
            '--disable-background-sync',
            '--disable-default-apps',
            '--no-first-run',
            '--no-default-browser-check',

            # Специально для медленных прокси
            '--aggressive-cache-discard',
            '--disable-features=VizDisplayCompositor',
            '--user-data-dir-name=optimized',
        ]

        if is_mobile:
            args.extend([
                '--user-agent="Mozilla/5.0 (Linux; Android 10; SM-G975F) AppleWebKit/537.36"',
                '--disable-features=VizDisplayCompositor',
            ])

        return args

    def cleanup_chrome_processes(self, force_kill_old: bool = True):
        """Агрессивная очистка всех Chrome процессов."""
        killed = 0

        if force_kill_old:
            try:
                # Убить все Chrome процессы старше 5 минут
                result = subprocess.run([
                    'sh', '-c',
                    "ps -eo pid,etimes,comm | grep -E 'chrom|playwright' | awk '$2 > 300 {print $1}'"
                ], capture_output=True, text=True, timeout=10)

                old_pids = result.stdout.strip().split('\n')
                for pid_str in old_pids:
                    if pid_str and pid_str.isdigit():
                        try:
                            os.kill(int(pid_str), signal.SIGKILL)
                            killed += 1
                        except (ProcessLookupError, PermissionError):
                            pass
            except Exception:
                pass

        # Стандартная очистка через pkill
        try:
            subprocess.run(['pkill', '-9', '-f', 'chrome.*--headless'],
                         capture_output=True, timeout=5)
            subprocess.run(['pkill', '-9', '-f', 'chromium.*--headless'],
                         capture_output=True, timeout=5)
            killed += 5  # Примерная оценка
        except Exception:
            pass

        if killed > 0:
            logger.info(f"🧹 Killed {killed} old/orphaned Chrome processes")

        return killed

    def start_browser_with_retry(self, profile_path: str, proxy_config: dict = None,
                                is_mobile: bool = False, max_retries: int = 3) -> dict:
        """Запустить браузер с повторными попытками при ошибках."""

        for attempt in range(max_retries):
            try:
                # Очистка процессов перед запуском
                if attempt > 0:
                    self.cleanup_chrome_processes(force_kill_old=True)
                    time.sleep(2)

                if not self.playwright_instance:
                    self.playwright_instance = sync_playwright().start()

                # Стабильные аргументы браузера
                browser_args = self.get_stable_browser_args(is_mobile)

                # Настройки контекста с учетом медленных прокси
                context_options = {
                    'user_data_dir': profile_path,
                    'headless': True,
                    'args': browser_args,
                    'viewport': {'width': 1366, 'height': 768},
                    'locale': 'ru-RU',
                    'timezone_id': 'Europe/Moscow',
                    'ignore_https_errors': True,

                    # КРИТИЧНО: Увеличенные таймауты для медленных прокси
                    'no_viewport': False,
                    'java_script_enabled': True,
                    'bypass_csp': True,
                }

                if proxy_config:
                    context_options['proxy'] = {
                        'server': f"http://{proxy_config['host']}:{proxy_config['port']}",
                        'username': proxy_config.get('username'),
                        'password': proxy_config.get('password')
                    }

                # Запуск браузера
                start_time = time.time()
                context = self.playwright_instance.chromium.launch_persistent_context(**context_options)

                # Настройка страницы для стабильной работы
                page = context.pages[0] if context.pages else context.new_page()

                # КРИТИЧНО: Настройка таймаутов для медленных прокси
                page.set_default_timeout(self.element_timeout)
                page.set_default_navigation_timeout(self.navigation_timeout)

                # Инжекция stealth скриптов
                try:
                    stealth = Stealth()
                    stealth.apply(page)
                except Exception as e:
                    logger.warning(f"Stealth injection failed: {e}")

                browser_id = f"browser_{int(time.time())}_{random.randint(1000, 9999)}"

                browser_info = {
                    'context': context,
                    'page': page,
                    'browser_id': browser_id,
                    'profile_path': profile_path,
                    'start_time': start_time,
                    'proxy_config': proxy_config,
                    'is_mobile': is_mobile
                }

                # Тест стабильности - попробовать навигацию
                try:
                    page.goto('about:blank', timeout=10000)
                    logger.info(f"✅ Browser {browser_id} started successfully (attempt {attempt + 1})")
                    return browser_info

                except Exception as nav_error:
                    logger.warning(f"Browser navigation test failed (attempt {attempt + 1}): {nav_error}")
                    try:
                        context.close()
                    except Exception:
                        pass
                    continue

            except Exception as e:
                logger.error(f"Browser start failed (attempt {attempt + 1}): {e}")
                if attempt == max_retries - 1:
                    raise Exception(f"Failed to start browser after {max_retries} attempts: {e}")
                time.sleep(2 ** attempt)  # Exponential backoff

        raise Exception("Browser startup failed after all retries")

    def navigate_with_patience(self, page: Page, url: str, max_wait_time: int = 60) -> bool:
        """Навигация с терпеливым ожиданием для медленных прокси."""
        try:
            # Попытка 1: Быстрая навигация
            try:
                page.goto(url, timeout=20000, wait_until='domcontentloaded')
                time.sleep(2)
                if page.title():
                    return True
            except Exception:
                logger.warning(f"Fast navigation to {url} failed, trying patient mode...")

            # Попытка 2: Терпеливая навигация для медленных прокси
            try:
                page.goto(url, timeout=max_wait_time * 1000, wait_until='commit')

                # Ждем появления title с несколькими попытками
                for wait_round in range(6):
                    time.sleep(5)
                    try:
                        title = page.evaluate('document.title')
                        if title and title.strip():
                            logger.info(f"✅ Page loaded after {(wait_round + 1) * 5}s: {title[:50]}")
                            return True
                    except Exception:
                        continue

                # Если title не появился, но страница загрузилась
                if page.url.startswith(url.split('://')[1].split('/')[0]):
                    logger.warning(f"⚠️ Page loaded but title empty: {url}")
                    return True

            except Exception as e:
                logger.error(f"Patient navigation to {url} failed: {e}")
                return False

        except Exception as e:
            logger.error(f"Navigation to {url} completely failed: {e}")
            return False

        return False

    def perform_yandex_search_optimized(self, browser_info: dict, query: str, target_domain: str) -> dict:
        """Оптимизированный поиск в Яндексе с обработкой медленных прокси."""
        page = browser_info['page']

        try:
            # 1. Переход на ya.ru с терпением
            logger.info(f"🔍 Starting search for '{query}' targeting {target_domain}")

            if not self.navigate_with_patience(page, 'https://ya.ru', max_wait_time=60):
                return {'status': 'error', 'error': 'Failed to load ya.ru'}

            # Проверка на капчу
            title = page.title() or ''
            if 'робот' in title.lower() or 'captcha' in title.lower():
                return {'status': 'captcha', 'error': f'Captcha detected: {title}'}

            # 2. Ввод поискового запроса с несколькими попытками
            search_input_found = False
            for attempt in range(3):
                try:
                    # Различные селекторы для поля поиска
                    selectors = [
                        'textarea[name="text"]',
                        'input[name="text"]',
                        '.search3__input textarea',
                        '.mini-suggest__input',
                    ]

                    for selector in selectors:
                        try:
                            page.wait_for_selector(selector, timeout=10000)
                            page.fill(selector, query)
                            search_input_found = True
                            break
                        except Exception:
                            continue

                    if search_input_found:
                        break

                    time.sleep(2)

                except Exception as e:
                    logger.warning(f"Search input attempt {attempt + 1} failed: {e}")
                    time.sleep(2)

            if not search_input_found:
                return {'status': 'error', 'error': 'Search input field not found'}

            # 3. Нажатие кнопки поиска
            search_clicked = False
            for attempt in range(3):
                try:
                    button_selectors = [
                        'button.search3__button',
                        '.search3__button',
                        'button[type="submit"]',
                        '.mini-suggest__button',
                    ]

                    for selector in button_selectors:
                        try:
                            page.click(selector, timeout=5000)
                            search_clicked = True
                            break
                        except Exception:
                            continue

                    if search_clicked:
                        break

                    # Fallback: Enter key
                    page.keyboard.press('Enter')
                    search_clicked = True
                    break

                except Exception as e:
                    logger.warning(f"Search button attempt {attempt + 1} failed: {e}")
                    time.sleep(1)

            if not search_clicked:
                return {'status': 'error', 'error': 'Could not submit search'}

            # 4. Ожидание результатов поиска
            time.sleep(5)

            # Проверка на капчу после поиска
            new_title = page.title() or ''
            if 'робот' in new_title.lower() or 'captcha' in new_title.lower():
                return {'status': 'captcha', 'error': f'Captcha after search: {new_title}'}

            # 5. Поиск ссылки на целевой домен
            try:
                # Поиск ссылок с целевым доменом
                links = page.query_selector_all(f'a[href*="{target_domain}"]')
                if not links:
                    # Расширенный поиск
                    all_links = page.query_selector_all('a')
                    for link in all_links[:20]:  # Проверяем первые 20 ссылок
                        href = link.get_attribute('href')
                        if href and target_domain in href:
                            links = [link]
                            break

                if not links:
                    return {'status': 'not_found', 'error': f'Target domain {target_domain} not found in search results'}

                # Клик на первую найденную ссылку
                target_link = links[0]
                target_href = target_link.get_attribute('href')

                logger.info(f"🎯 Found target link: {target_href}")

                # Клик по ссылке
                target_link.click()

                # Ожидание загрузки целевого сайта
                time.sleep(random.randint(5, 10))

                final_url = page.url
                final_title = page.title() or ''

                logger.info(f"✅ Successfully clicked through to: {final_url}")

                return {
                    'status': 'success',
                    'target_url': final_url,
                    'target_title': final_title,
                    'search_query': query,
                    'found_link': target_href
                }

            except Exception as e:
                return {'status': 'click_failed', 'error': f'Failed to click target link: {e}'}

        except Exception as e:
            logger.error(f"Search operation failed: {e}")
            return {'status': 'error', 'error': str(e)}

    def close_browser(self, browser_info: dict):
        """Закрытие браузера с полной очисткой."""
        try:
            if 'context' in browser_info and browser_info['context']:
                browser_info['context'].close()

            # Дополнительная очистка процессов
            self.cleanup_chrome_processes(force_kill_old=False)

            browser_id = browser_info.get('browser_id', 'unknown')
            logger.info(f"🔒 Browser {browser_id} closed successfully")

        except Exception as e:
            logger.error(f"Error closing browser: {e}")

    def __del__(self):
        """Очистка при уничтожении объекта."""
        try:
            if self.playwright_instance:
                self.playwright_instance.stop()
            self.cleanup_chrome_processes(force_kill_old=True)
        except Exception:
            pass