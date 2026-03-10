"""
Yandex Maps profile visiting tasks.
"""
import os
import time
import random
import logging
import re
from typing import Dict, List, Optional
from urllib.parse import urlparse, parse_qs
from datetime import datetime

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, WebDriverException,
    ElementClickInterceptedException
)

from app.database import get_db_session, get_setting
from app.models import BrowserProfile, Task
from app.models.profile_target_visit import ProfileTargetVisit
from core import BrowserManager, ProxyManager, CaptchaSolver
from core.capsola_solver import create_capsola_solver
from app.config import settings
from .celery_app import BaseTask


def _update_task_log(profile_id: int, target_url: str, message: str, status: str = None, error: str = None, result_data: dict = None, exec_time: float = None, task_id: int = None):
    """Update the task in DB with log entry and optionally status.
    
    If task_id is provided, update that exact task. Otherwise fall back to searching by profile_id + target_url.
    """
    try:
        with get_db_session() as db:
            task_obj = None
            
            # Prefer direct lookup by task_id
            if task_id:
                task_obj = db.query(Task).filter(Task.id == task_id).first()
            
            # Fallback: find by profile_id + target_url
            if not task_obj:
                task_obj = db.query(Task).filter(
                    Task.profile_id == profile_id,
                    Task.target_url == target_url,
                    Task.task_type == 'yandex_visit',
                    Task.status.notin_(['completed', 'failed'])
                ).order_by(Task.created_at.desc()).first()
            
            # If setting to completed/failed, allow finding in_progress tasks
            if not task_obj and status in ('completed', 'failed'):
                task_obj = db.query(Task).filter(
                    Task.profile_id == profile_id,
                    Task.target_url == target_url,
                    Task.task_type == 'yandex_visit'
                ).order_by(Task.created_at.desc()).first()
            
            if task_obj:
                task_obj.add_log(message)
                if status:
                    task_obj.status = status
                if status == 'in_progress' and not task_obj.started_at:
                    task_obj.started_at = datetime.utcnow()
                if error:
                    task_obj.error_message = error
                if result_data:
                    task_obj.result = result_data
                if exec_time:
                    task_obj.execution_time_seconds = exec_time
                if status in ('completed', 'failed'):
                    task_obj.completed_at = datetime.utcnow()
                db.commit()
    except Exception as e:
        logger.warning(f"Failed to update task log: {e}")

logger = logging.getLogger(__name__)


@shared_task(base=BaseTask, bind=True, max_retries=2, default_retry_delay=30, soft_time_limit=300, time_limit=330)
def visit_yandex_maps_profile_task(self, profile_id: int, target_url: str, visit_parameters: Dict = None, task_id: int = None, search_query: str = None):
    """
    Visit a Yandex Maps profile and perform realistic interactions.

    Args:
        profile_id: Browser profile to use
        target_url: Yandex Maps profile URL
        visit_parameters: Custom parameters for the visit
        task_id: DB Task record ID for precise status tracking
        search_query: If provided, search for company on maps instead of direct URL navigation
    """
    browser_manager = None
    browser_id = None
    _profile_dir_for_cleanup = None  # Track profile dir for cleanup even if browser_id is None

    try:
        # Validate parameters
        if not target_url or 'yandex' not in target_url.lower():
            raise ValueError("Invalid Yandex Maps URL provided")

        # Default visit parameters
        default_params = {
            'min_visit_time': get_setting('yandex_visit_min_time', 10),
            'max_visit_time': get_setting('yandex_visit_max_time', 20),
            'actions': get_setting('yandex_actions_enabled', [
                'scroll', 'view_photos', 'read_reviews', 'click_contacts', 'view_map'
            ]),
            'scroll_probability': 0.9,
            'photo_click_probability': 0.7,
            'review_read_probability': 0.8,
            'contact_click_probability': 0.5,
            'map_interaction_probability': 0.6
        }

        if visit_parameters:
            default_params.update(visit_parameters)

        # Get profile from database
        with get_db_session() as db:
            profile_obj = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
            if not profile_obj:
                raise ValueError(f"Profile {profile_id} not found")

            if not profile_obj.is_ready_for_tasks():
                raise ValueError(f"Profile {profile_id} is not ready for tasks. Complete warmup first.")

            # Store profile data before session closes
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
            
            # Update profile status
            profile_obj.last_used_at = datetime.utcnow()
            db.commit()

        logger.info(f"Starting Yandex Maps visit for profile {profile_id}: {target_url}")
        _update_task_log(profile_id, target_url, f"🚀 Запуск визита профилем {profile_data_from_db['name']}", status='in_progress', task_id=task_id)

        # Initialize managers
        browser_manager = BrowserManager()
        
        # Guard: check how many Chrome processes are already running
        try:
            import subprocess as _sp
            chrome_count = int(_sp.run(['sh', '-c', 'pgrep -c chrome || echo 0'], capture_output=True, text=True, timeout=5).stdout.strip())
            if chrome_count > 50:
                logger.warning(f"⚠️ Too many Chrome processes ({chrome_count}), cleaning up before launching new one")
                from core.browser_manager import cleanup_orphaned_chrome
                cleanup_orphaned_chrome()
                time.sleep(2)
        except Exception:
            pass
        
        proxy_manager = ProxyManager()
        proxy_manager.load_proxies_from_db()
        captcha_solver = CaptchaSolver()

        # Get proxy for profile
        proxy_data = None
        if profile_data_from_db['proxy_host'] and profile_data_from_db['proxy_port']:
            proxy_data = {
                'host': profile_data_from_db['proxy_host'],
                'port': profile_data_from_db['proxy_port'],
                'username': profile_data_from_db['proxy_username'],
                'password': profile_data_from_db['proxy_password'],
                'proxy_type': profile_data_from_db['proxy_type'] or 'http'
            }
            logger.info(f"📡 Proxy from profile: {proxy_data['proxy_type']}://{proxy_data['host']}:{proxy_data['port']} (user={proxy_data.get('username', 'none')})")
        else:
            proxy_data = proxy_manager.get_available_proxy()
            if proxy_data:
                logger.info(f"📡 Proxy from manager: {proxy_data}")

        if proxy_data:
            logger.info(f"Using proxy: {proxy_data['host']}:{proxy_data['port']}")
        else:
            error_msg = "🚫 Нет доступных прокси! Визит без прокси запрещён."
            logger.error(error_msg)
            _update_task_log(profile_id, target_url, error_msg, status='failed', task_id=task_id)
            return {'status': 'error', 'error': error_msg, 'profile_id': profile_id, 'target_url': target_url}

        # Create profile data
        from core.profile_generator import ProfileGenerator
        profile_generator = ProfileGenerator()
        
        # Detect if this is a mobile profile based on user agent
        ua_str = profile_data_from_db['user_agent']
        is_mobile = 'Mobile' in ua_str and 'Android' in ua_str
        
        profile_data = profile_generator.generate_profile(profile_data_from_db['name'], is_mobile=is_mobile)

        # Update with database values
        # Force ru-RU language for Yandex visits — prevents redirect to yandex.com
        profile_data.update({
            'user_agent': profile_data_from_db['user_agent'],
            'viewport': {
                'width': profile_data_from_db['viewport_width'],
                'height': profile_data_from_db['viewport_height']
            },
            'timezone': profile_data_from_db['timezone'],
            'language': 'ru-RU'
        })
        
        if is_mobile:
            logger.info(f"📱 Mobile profile detected: {profile_data_from_db['name']}")
        
        # Track profile dir for cleanup even if Chrome fails to start
        from app.config import settings as _settings
        _profile_dir_for_cleanup = os.path.join(_settings.browser_user_data_dir, profile_data['name'])

        # Create browser session
        browser_id = browser_manager.create_browser_session(profile_data, proxy_data)
        driver = browser_manager.active_browsers[browser_id]

        # Visit Yandex Maps profile
        start_time = time.time()

        # === Navigation: Search-based or Direct URL ===
        if search_query:
            # NEW: Search for company on Yandex Maps, then click the card
            logger.info(f"🔍 Using SEARCH-BASED navigation: '{search_query}'")
            _update_task_log(profile_id, target_url, f"🔍 Поиск компании на Яндекс Картах: '{search_query}'", task_id=task_id)
            
            search_result = search_company_on_maps(
                driver=driver,
                search_query=search_query,
                target_url=target_url,
                captcha_solver=captcha_solver,
                task_id=task_id,
                profile_id=profile_id
            )
            
            if not search_result['success']:
                error_msg = search_result.get('error', 'Company not found via search')
                logger.error(f"❌ Search-based navigation failed: {error_msg}")
                _update_task_log(profile_id, target_url, f"❌ Поиск не удался: {error_msg[:150]}", status='failed', error=error_msg, task_id=task_id)
                raise Exception(f"Search navigation failed: {error_msg}")
            
            actual_url = search_result.get('found_url', driver.current_url)
            logger.info(f"✅ Search navigation succeeded: {actual_url[:120]}")
            _update_task_log(profile_id, target_url, f"✅ Компания найдена через поиск: {actual_url[:100]}", task_id=task_id)
        else:
            # ORIGINAL: Direct URL navigation
            _update_task_log(profile_id, target_url, "🌐 Открываем страницу...", task_id=task_id)
            if not browser_manager.navigate_to_url(browser_id, target_url, timeout=90):
                _update_task_log(profile_id, target_url, "❌ Не удалось открыть страницу", status='failed', error='Navigation failed', task_id=task_id)
                raise Exception("Failed to navigate to Yandex Maps profile")

            actual_url = driver.current_url
            _update_task_log(profile_id, target_url, f"✅ Страница загружена: {actual_url[:120]}", task_id=task_id)
            logger.info(f"📍 Requested URL: {target_url}")
            logger.info(f"📍 Actual URL after load: {actual_url}")
            
            # Если всё равно произошёл редирект .ru → .com — логируем как предупреждение
            if 'yandex.com' in actual_url and 'yandex.ru' in target_url:
                logger.warning(f"⚠️ Yandex redirected .ru → .com despite ru-RU language — possible proxy geo issue")
                _update_task_log(profile_id, target_url, "⚠️ Редирект на yandex.com — возможно прокси определяется как не-RU", task_id=task_id)

            # Wait for page to load (direct URL path)
            time.sleep(random.uniform(2, 4))

            # Check for captcha or blocks (direct URL path)
            if detect_captcha_or_block(driver):
                logger.warning("Captcha or block detected, attempting to solve")
                _update_task_log(profile_id, target_url, "⚠️ Обнаружена капча, решаем через Capsola...", task_id=task_id)
                if not handle_yandex_protection(driver, captcha_solver):
                    _update_task_log(profile_id, target_url, "❌ Не удалось решить капчу", status='failed', error='Captcha not solved', task_id=task_id)
                    raise Exception("Unable to bypass Yandex protection")
                _update_task_log(profile_id, target_url, "✅ Капча решена!", task_id=task_id)

        # Take initial screenshot
        if settings.save_screenshots:
            browser_manager.take_screenshot(browser_id, f"yandex_visit_{profile_id}_start.png")

        # Perform realistic visit actions
        _update_task_log(profile_id, target_url, "🎯 Выполняем действия на странице...", task_id=task_id)
        visit_results = perform_yandex_visit_actions(
            browser_manager,
            browser_id,
            default_params
        )

        # Calculate visit duration
        visit_duration = time.time() - start_time
        target_duration = random.randint(default_params['min_visit_time'], default_params['max_visit_time'])

        # Stay longer if needed
        if visit_duration < target_duration:
            remaining_time = target_duration - visit_duration
            logger.info(f"Staying on page for additional {remaining_time:.1f} seconds")

            # Passive browsing for remaining time
            perform_passive_browsing(browser_manager, browser_id, remaining_time)

        # Take final screenshot
        if settings.save_screenshots:
            browser_manager.take_screenshot(browser_id, f"yandex_visit_{profile_id}_end.png")

        # Final visit duration
        total_duration = time.time() - start_time

        # Update profile statistics
        with get_db_session() as db:
            profile_obj = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
            if profile_obj:
                profile_obj.update_session_stats(success=True)
                db.commit()

        # Update proxy statistics if used
        if proxy_data and 'id' in proxy_data:
            proxy_manager.update_proxy_stats(proxy_data['id'], True, response_time=total_duration*1000)

        result = {
            "status": "success",
            "profile_id": profile_id,
            "target_url": target_url,
            "visit_duration": total_duration,
            "actions_performed": visit_results.get('actions_performed', []),
            "elements_interacted": visit_results.get('elements_interacted', 0),
            "scroll_actions": visit_results.get('scroll_actions', 0),
            "clicks_performed": visit_results.get('clicks_performed', 0),
            "proxy_used": proxy_data['host'] if proxy_data else None
        }

        logger.info(f"Yandex Maps visit completed successfully: {result}")
        _update_task_log(profile_id, target_url, f"🎉 Визит завершён! Время: {total_duration:.0f}с", status='completed', result_data=result, exec_time=total_duration, task_id=task_id)
        
        # Update target statistics
        try:
            with get_db_session() as db:
                from app.models import YandexMapTarget
                target_obj = db.query(YandexMapTarget).filter(YandexMapTarget.url == target_url).first()
                if target_obj:
                    target_obj.total_visits = (target_obj.total_visits or 0) + 1
                    target_obj.successful_visits = (target_obj.successful_visits or 0) + 1
                    target_obj.today_visits = (target_obj.today_visits or 0) + 1
                    target_obj.today_successful = (target_obj.today_successful or 0) + 1
                    # Don't overwrite last_visit_at here — the scheduler sets it
                    # at dispatch time, so interval checks stay consistent.
                    
                    # Record profile-target visit (one profile visits one target only once)
                    existing_visit = db.query(ProfileTargetVisit).filter(
                        ProfileTargetVisit.profile_id == profile_id,
                        ProfileTargetVisit.target_id == target_obj.id
                    ).first()
                    if not existing_visit:
                        visit_record = ProfileTargetVisit(
                            profile_id=profile_id,
                            target_id=target_obj.id,
                            status="completed",
                            visited_at=datetime.utcnow()
                        )
                        db.add(visit_record)
                    else:
                        existing_visit.status = "completed"
                        existing_visit.visited_at = datetime.utcnow()
                    
                    db.commit()
                    logger.info(f"✅ Recorded profile-target visit: profile={profile_id}, target={target_obj.id}")
        except Exception as e:
            logger.warning(f"Failed to update target stats: {e}")
        
        return result

    except SoftTimeLimitExceeded:
        logger.error(f"⏰ Soft time limit exceeded for profile {profile_id}, cleaning up Chrome...")
        _update_task_log(profile_id, target_url, "⏰ Превышено время выполнения задачи", status='failed', error='SoftTimeLimitExceeded', task_id=task_id)
        raise

    except Exception as e:
        logger.error(f"Error visiting Yandex Maps profile {profile_id}: {e}")
        _update_task_log(profile_id, target_url, f"❌ Ошибка: {str(e)[:200]}", status='failed', error=str(e)[:500], task_id=task_id)
        
        # Update target failure stats
        try:
            with get_db_session() as db:
                from app.models import YandexMapTarget
                target_obj = db.query(YandexMapTarget).filter(YandexMapTarget.url == target_url).first()
                if target_obj:
                    target_obj.total_visits = (target_obj.total_visits or 0) + 1
                    target_obj.failed_visits = (target_obj.failed_visits or 0) + 1
                    target_obj.today_visits = (target_obj.today_visits or 0) + 1
                    target_obj.today_failed = (target_obj.today_failed or 0) + 1
                    db.commit()
        except:
            pass

        # Update profile with failure
        try:
            with get_db_session() as db:
                profile_obj = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
                if profile_obj:
                    profile_obj.update_session_stats(success=False)
                    db.commit()
        except:
            pass

        # Update proxy with failure if used — but only for actual proxy errors,
        # not Chrome crashes or resource issues
        try:
            if proxy_data and 'id' in proxy_data:
                error_str_lower = str(e).lower()
                is_proxy_error = any(x in error_str_lower for x in [
                    'proxy', 'tunnel', 'socks', 'err_proxy',
                    'proxy connection', 'authentication required',
                ])
                is_browser_error = any(x in error_str_lower for x in [
                    'unexpectedly exited', 'session not created',
                    'connection refused', 'chrome not reachable',
                    'oom', 'out of memory', 'status code was: -9',
                    'devtoolsactiveport', 'cannot find chrome',
                    'unable to bypass', 'navigation failed',
                    'timeout', 'timed out',
                ])
                if is_proxy_error or not is_browser_error:
                    proxy_manager.update_proxy_stats(proxy_data['id'], False, error_message=str(e))
                else:
                    logger.info(f"Skipping proxy failure report — browser error, not proxy: {str(e)[:100]}")
        except:
            pass

        # Retry task if possible (but not for Chrome resource issues)
        error_str = str(e).lower()
        is_resource_error = any(x in error_str for x in ['oom', 'out of memory'])
        is_proxy_error_retry = any(x in error_str for x in ['err_tunnel', 'err_proxy', 'err_connection'])
        # Transient Chrome errors (session not created, chrome not reachable) — retry once
        is_transient_chrome = any(x in error_str for x in ['session not created', 'chrome not reachable', 'connection refused'])
        if is_transient_chrome and self.request.retries < self.max_retries:
            logger.warning(f"🔄 Transient Chrome error, retrying in 15s (attempt {self.request.retries + 1}/{self.max_retries})")
            raise self.retry(exc=e, countdown=15)
        if is_proxy_error_retry and self.request.retries < self.max_retries:
            # Exponential backoff for proxy errors: 60s, 120s
            backoff = 60 * (2 ** self.request.retries)
            logger.warning(f"🔄 Proxy error, retrying in {backoff}s (attempt {self.request.retries + 1}/{self.max_retries})")
            raise self.retry(exc=e, countdown=backoff)
        if not is_resource_error and self.request.retries < self.max_retries:
            # Use different proxy on retry
            raise self.retry(exc=e)

        raise e

    finally:
        # Cleanup browser session
        if browser_manager and browser_id:
            try:
                browser_manager.close_browser_session(browser_id)
            except Exception as e:
                logger.error(f"Error closing browser session: {e}")
        elif _profile_dir_for_cleanup:
            # browser_id is None — Chrome failed to start but may have left orphans.
            # Clean up any Chrome processes for this profile directory.
            try:
                browser_manager_cleanup = browser_manager or BrowserManager.__new__(BrowserManager)
                if hasattr(browser_manager_cleanup, '_kill_chrome_by_profile_dir'):
                    browser_manager_cleanup._kill_chrome_by_profile_dir(_profile_dir_for_cleanup)
                else:
                    import subprocess as _sp
                    _sp.run(['pkill', '-9', '-f', _profile_dir_for_cleanup], capture_output=True, timeout=5)
            except Exception as cleanup_err:
                logger.warning(f"Cleanup by profile dir failed: {cleanup_err}")
        # Note: Do NOT call cleanup_orphaned_chrome() here — it kills ALL Chrome
        # processes including those used by other concurrent tasks, causing -9 errors.
        # close_browser_session() already kills Chrome by PID for this specific session.


def _url_path_has_captcha(url: str) -> bool:
    """Check if the URL path (ignoring query params like utm_referrer) contains captcha indicators.
    
    IMPORTANT: We MUST check only the path, not query parameters.
    After captcha is solved, Yandex redirects to search results with
    utm_referrer=https://ya.ru/showcaptcha... — the word 'showcaptcha'
    appears in the query string but the page itself is NOT a captcha page.
    Checking the full URL causes false positives that break captcha solving.
    """
    try:
        url_lower = url.lower()
        # Extract just the base URL (path) before query parameters
        url_path = url_lower.split('?')[0]
        return any(indicator in url_path for indicator in ['showcaptcha', 'checkcaptcha', '/captcha', 'blocked', 'verify'])
    except Exception:
        return False


def detect_captcha_or_block(driver) -> bool:
    """Detect if we've been blocked or shown a captcha."""
    try:
        # First check URL path — most reliable indicator
        # IMPORTANT: Only check path, NOT query params (utm_referrer contains 'showcaptcha' on normal pages)
        try:
            current_url = driver.current_url.lower()
        except Exception as _url_err:
            _url_str = str(_url_err)
            if 'Timed out' in _url_str or 'timeout' in _url_str.lower():
                logger.warning(f"⚠️ Renderer timeout in detect_captcha_or_block (current_url) — waiting...")
                time.sleep(10)
                try:
                    current_url = driver.current_url.lower()
                except Exception:
                    logger.warning("⚠️ Renderer still dead in detect_captcha_or_block — assuming no captcha")
                    return False
            else:
                raise
        if _url_path_has_captcha(current_url):
            logger.info(f"🔍 URL indicates captcha: {current_url[:100]}")
            return True

        # Check for specific captcha elements (most reliable after URL)
        captcha_selectors = [
            "div[class*='CheckboxCaptcha']",
            "div[class*='AdvancedCaptcha']",
            "div[class*='AdvancedCaptcha_silhouette']",
            "[class*='SmartCaptcha']",
            "[class*='SilhouetteTask']",
            ".form-captcha",
            ".check-robot",
            "iframe[src*='captcha']",
            "iframe[src*='smartcaptcha']",
        ]

        for selector in captcha_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                if elements and any(el.is_displayed() for el in elements):
                    logger.info(f"🔍 Found captcha element: {selector}")
                    return True
            except:
                continue

        # Check page title
        try:
            page_title = driver.title.lower()
            if any(word in page_title for word in ['captcha', 'robot', 'verification', 'проверка']):
                logger.info(f"🔍 Title indicates captcha: {page_title}")
                return True
        except:
            pass

        # Check visible text for captcha indicators (NOT raw page source)
        # This avoids false positives from script URLs like captchapgrd
        try:
            body_text = driver.find_element(By.TAG_NAME, "body").text.lower()
            captcha_texts = [
                "я не робот", "i'm not a robot", "i am not a robot",
                "проверка безопасности", "security check",
                "доступ запрещен", "access denied",
                "подтвердите, что вы не робот",
            ]
            for text in captcha_texts:
                if text in body_text:
                    logger.info(f"🔍 Found captcha text in body: '{text}'")
                    return True
        except:
            pass

        return False

    except Exception as e:
        logger.warning(f"Error detecting captcha/block: {e}")
        return False


def handle_yandex_protection(driver, captcha_solver: CaptchaSolver, max_kaleidoscope_attempts: int = 7) -> bool:
    """Handle Yandex captcha or protection mechanisms (SmartCaptcha через Capsola)."""
    try:
        logger.info("🔧 Attempting to handle Yandex protection")
        
        # Делаем скриншот для анализа
        screenshot_path = f"screenshots/captcha_debug_{int(time.time())}.png"
        try:
            driver.save_screenshot(screenshot_path)
            logger.info(f"📸 Captcha screenshot saved: {screenshot_path}")
            # Save page source for debug
            html_path = screenshot_path.replace('.png', '.html')
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(driver.page_source)
            logger.info(f"📄 Page source saved: {html_path}")
        except Exception as e:
            logger.warning(f"Failed to save screenshot: {e}")

        # Проверяем наличие Capsola API
        logger.info(f"🔑 Capsola check: enabled={settings.capsola_enabled}, key='{settings.capsola_api_key[:8]}...' (len={len(settings.capsola_api_key)})")
        if not settings.capsola_enabled or not settings.capsola_api_key:
            logger.warning("⚠️ Capsola не настроен — пропускаем решение капчи")
            return _try_simple_refresh(driver)

        # === ШАГ 1: Определяем тип капчи ===
        try:
            current_url = driver.current_url.lower()
            page_source = driver.page_source
        except Exception as _init_err:
            _init_str = str(_init_err)
            if 'Timed out' in _init_str or 'timeout' in _init_str.lower():
                logger.warning(f"⚠️ Renderer timeout in handle_yandex_protection initialization — waiting for recovery...")
                time.sleep(10)
                try:
                    current_url = driver.current_url.lower()
                    page_source = driver.page_source
                except Exception:
                    logger.error("❌ Renderer still dead — cannot solve captcha")
                    return False
            else:
                raise
        page_source_lower = page_source.lower()
        logger.info(f"🔍 URL: {current_url[:120]}")
        
        # ============================================
        # YANDEX KALEIDOSCOPE (slider puzzle) — check before silhouette/smartcaptcha
        # ============================================
        is_kaleidoscope = (
            'kaleidoscope' in page_source_lower or
            'captchaslider' in page_source_lower or
            'kaleidoscopecanvas' in page_source_lower or
            '/ru/kaleidoscope' in page_source_lower
        )
        
        if is_kaleidoscope:
            logger.info("🧩 Kaleidoscope (slider puzzle) detected! Solving via Capsola PazlCaptcha API...")
            result = _solve_yandex_kaleidoscope_captcha(
                driver,
                screenshot_path,
                max_attempts=max_kaleidoscope_attempts,
            )
            try:
                driver.set_page_load_timeout(60)
                driver.set_script_timeout(30)
            except Exception:
                pass
            return result
        
        # ============================================
        # YANDEX SILHOUETTE / PAZL CAPTCHA (priority — detected before SmartCaptcha)
        # ============================================
        is_silhouette = (
            'advancedcaptcha_silhouette' in page_source_lower or
            'advancedcaptcha-silhouettetask' in page_source_lower or
            'silhouette-container' in page_source_lower or
            '/silhouette' in page_source_lower or
            'silhouettecaptcha' in page_source_lower
        )
        
        if is_silhouette:
            logger.info("🧩 Silhouette/PazlCaptcha detected! Solving via Capsola PazlCaptcha API...")
            result = _solve_yandex_silhouette_captcha(driver, screenshot_path)
            try:
                driver.set_page_load_timeout(60)
                driver.set_script_timeout(30)
            except Exception:
                pass
            return result
        
        # ============================================
        # YANDEX SMARTCAPTCHA (showcaptcha page OR embedded)
        # ============================================
        # Check only URL path for captcha indicators (not query params like utm_referrer)
        url_path_for_check = current_url.split('?')[0]
        is_captcha_page = 'showcaptcha' in url_path_for_check or '/captcha' in url_path_for_check
        is_smartcaptcha_in_source = any(kw in page_source_lower for kw in [
            'smartcaptcha', 'checkboxcaptcha', 'checkbox-captcha', 
            'captcha-api.yandex', 'i\'m not a robot', 'я не робот',
            'advancedcaptcha', 'captcha'
        ])
        
        logger.info(f"🔍 Captcha detection: url_match={is_captcha_page}, source_match={is_smartcaptcha_in_source}")
        
        if is_captcha_page or is_smartcaptcha_in_source:
            logger.info(f"🎯 SmartCaptcha detected (url={is_captcha_page}, source={is_smartcaptcha_in_source})")
            result = _solve_yandex_showcaptcha(driver, screenshot_path, max_kaleidoscope_attempts=max_kaleidoscope_attempts)
            # Restore normal timeout after captcha solving (captcha sets 120s)
            try:
                driver.set_page_load_timeout(60)
                driver.set_script_timeout(30)
            except Exception:
                pass
            return result
        
        # ============================================
        # SMARTCAPTCHA (embedded on page via iframe)
        # ============================================
        smartcaptcha_selectors = [
            "iframe[src*='smartcaptcha']",
            "iframe[src*='captcha-api.yandex']",
            "div[class*='SmartCaptcha']",
            "div[class*='CheckboxCaptcha']",
        ]
        for selector in smartcaptcha_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                if elements:
                    logger.info(f"🎯 Embedded SmartCaptcha found: {selector}")
                    result = _solve_yandex_showcaptcha(driver, screenshot_path, max_kaleidoscope_attempts=max_kaleidoscope_attempts)
                    try:
                        driver.set_page_load_timeout(60)
                        driver.set_script_timeout(30)
                    except Exception:
                        pass
                    return result
            except:
                continue
        
        # ============================================
        # CLASSIC IMAGE CAPTCHA
        # ============================================
        captcha_img = None
        try:
            captcha_img = driver.find_element(By.CSS_SELECTOR, ".captcha__image img, .form-captcha__image img")
        except:
            pass

        if captcha_img:
            logger.info("🔍 Found image captcha, attempting to solve")
            img_data = captcha_solver.capture_element_screenshot(driver, captcha_img)
            if img_data:
                solution = captcha_solver.solve_image_captcha(img_data)
                if solution:
                    captcha_input = driver.find_element(By.CSS_SELECTOR, ".captcha__control input, .form-captcha__input")
                    captcha_input.clear()
                    captcha_input.send_keys(solution)
                    submit_btn = driver.find_element(By.CSS_SELECTOR, ".captcha__submit, .form-captcha__submit")
                    submit_btn.click()
                    time.sleep(5)
                    if not detect_captcha_or_block(driver):
                        logger.info("✅ Image captcha solved successfully")
                        return True

        # Fallback: простое обновление
        return _try_simple_refresh(driver)

    except Exception as e:
        logger.error(f"Error handling Yandex protection: {e}")
        import traceback
        traceback.print_exc()
        return False


def _solve_yandex_showcaptcha(driver, screenshot_path: str, max_kaleidoscope_attempts: int = 7) -> bool:
    """Solve Yandex SmartCaptcha using Capsola API.
    
    Flow:
    1. Click "I'm not a robot" checkbox
    2. Wait for either: captcha resolved OR image grid challenge appears
    3. If image grid appears: screenshot elements, send to Capsola, click coordinates
    4. If still blocked: try full screenshot approach
    """
    from app.config import settings
    from core.capsola_solver import create_capsola_solver
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    
    try:
        capsola = create_capsola_solver(settings.capsola_api_key)
        
        # ШАГ 1: Click the "I'm not a robot" checkbox
        logger.info("🔍 Looking for SmartCaptcha checkbox...")
        
        # First, simulate human-like mouse movement on the page
        try:
            body = driver.find_element(By.TAG_NAME, "body")
            # Random mouse movements before clicking checkbox (like a human would)
            for _ in range(random.randint(2, 4)):
                x_off = random.randint(-200, 200)
                y_off = random.randint(-100, 100)
                try:
                    ActionChains(driver).move_to_element_with_offset(body, 300 + x_off, 300 + y_off).perform()
                    time.sleep(random.uniform(0.2, 0.6))
                except:
                    pass
        except:
            pass
        
        time.sleep(random.uniform(1, 2))
        
        checkbox_clicked = False
        checkbox_selectors = [
            ".CheckboxCaptcha-Button",
            "[class*='CheckboxCaptcha'] button",
            "button[class*='CheckboxCaptcha']",
            "[class*='checkbox-captcha'] button",
            "[class*='checkbox-captcha'] input",
            "input[type='checkbox']",
        ]
        
        for selector in checkbox_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for el in elements:
                    if el.is_displayed():
                        # Move to checkbox with slight offset (human-like)
                        offset_x = random.randint(-5, 5)
                        offset_y = random.randint(-3, 3)
                        ActionChains(driver)\
                            .move_to_element_with_offset(el, offset_x, offset_y)\
                            .pause(random.uniform(0.3, 0.8))\
                            .click()\
                            .perform()
                        checkbox_clicked = True
                        logger.info(f"✅ Clicked checkbox: {selector}")
                        break
            except:
                continue
            if checkbox_clicked:
                break
        
        if not checkbox_clicked:
            # Try submitting the form directly via JS  
            logger.info("⚠️ No checkbox found, trying form submit via JS...")
            try:
                driver.execute_script("""
                    var form = document.getElementById('checkbox-captcha-form');
                    if (form) { 
                        // Click the button first to trigger PoW
                        var btn = form.querySelector('.CheckboxCaptcha-Button, input[type=submit]');
                        if (btn) btn.click();
                    }
                """)
                checkbox_clicked = True
                logger.info("✅ Clicked checkbox via JS form")
            except Exception as e:
                logger.warning(f"JS form click failed: {e}")
        
        # ШАГ 2: Wait for reaction — PoW completes and form auto-submits
        logger.info("⏳ Waiting for SmartCaptcha PoW + redirect...")
        
        # Set moderate timeouts for captcha solving. Don't go too high (300s) because
        # it blocks the worker if Chrome dies. 120s is enough for PoW + proxy latency.
        # IMPORTANT: caller (handle_yandex_protection) MUST restore timeout to 60 after return.
        try:
            driver.set_page_load_timeout(120)
        except Exception as e:
            logger.warning(f"Could not set page_load_timeout: {e}")
        try:
            driver.set_script_timeout(120)
        except Exception as e:
            logger.warning(f"Could not set script_timeout: {e}")
        
        # Save pre-click URL to detect redirect
        pre_click_url = driver.current_url
        
        # Wait up to 25 seconds for URL change (reduced from 60).
        # SmartCaptcha checkbox PoW has ~2% success rate — if it works, it works within 15-20s.
        # Don't waste 60s waiting — better to refresh and try kaleidoscope (100% solvable).
        redirected = False
        driver_alive = True
        image_grid_appeared = False
        for i in range(25):
            time.sleep(1)
            try:
                new_url = driver.current_url
                if new_url != pre_click_url:
                    # IMPORTANT: Check only URL path, not query params!
                    # After captcha solve, search URL may contain showcaptcha in utm_referrer
                    new_url_path = new_url.lower().split('?')[0]
                    if 'showcaptcha' not in new_url_path and 'checkcaptcha' not in new_url_path:
                        logger.info(f"🎉 Page redirected after {i}s! New URL: {new_url[:100]}")
                        redirected = True
                        break
                    elif 'checkcaptcha' in new_url_path:
                        logger.info(f"⏳ Form auto-submitted ({i}s), waiting for final redirect...")
                        continue
                # Check if image grid or kaleidoscope appeared — means PoW was accepted
                # but additional challenge is required
                if i > 0 and i % 5 == 0:
                    try:
                        captcha_type_check = driver.execute_script("""
                            var src = document.documentElement.innerHTML.toLowerCase();
                            return {
                                grid: !!(document.querySelector("[class*='AdvancedCaptcha']") ||
                                         document.querySelector("[class*='Task-Grid']") ||
                                         document.querySelector("canvas[class*='captcha']")),
                                kaleidoscope: (src.indexOf('kaleidoscope') !== -1 ||
                                              src.indexOf('captchaslider') !== -1)
                            };
                        """)
                        if captcha_type_check and captcha_type_check.get('kaleidoscope'):
                            logger.info(f"🧩 Kaleidoscope appeared at {i}s after checkbox — switching to PazlCaptcha solver!")
                            return _solve_yandex_kaleidoscope_captcha(driver, screenshot_path, max_attempts=max_kaleidoscope_attempts)
                        if captcha_type_check and captcha_type_check.get('grid'):
                            logger.info(f"🔍 Image grid appeared at {i}s — moving to ШАГ 3")
                            image_grid_appeared = True
                            break
                    except Exception:
                        pass
                # Log progress every 10 seconds
                if i > 0 and i % 10 == 0:
                    logger.info(f"⏳ Still waiting for PoW... ({i}s elapsed, URL unchanged)")
            except Exception as e:
                err_str = str(e)
                if 'Connection refused' in err_str or 'RemoteDisconnected' in err_str or 'ConnectionResetError' in err_str:
                    logger.error(f"💀 Chrome/chromedriver DIED at {i}s after checkbox click: {err_str[:200]}")
                    driver_alive = False
                    break
                else:
                    logger.warning(f"⚠️ current_url error at {i}s: {type(e).__name__}: {err_str[:200]}")
        
        if not driver_alive:
            logger.error("💀 Browser died during SmartCaptcha PoW wait — cannot continue")
            return False
        
        if redirected:
            time.sleep(2)
            if not detect_captcha_or_block(driver):
                logger.info("🎉 Captcha passed after checkbox click + redirect!")
                return True
        
        # Save page source after click for debug
        try:
            after_html = f"screenshots/captcha_after_click_{int(time.time())}.html"
            with open(after_html, 'w', encoding='utf-8') as f:
                f.write(driver.page_source)
            after_ss = f"screenshots/captcha_after_click_{int(time.time())}.png"
            driver.save_screenshot(after_ss)
            logger.info(f"📄 After-click state saved: {after_html}")
        except Exception as e:
            logger.warning(f"⚠️ Could not save after-click debug: {type(e).__name__}: {str(e)[:200]}")
        
        # ШАГ 3: Check if image grid challenge appeared
        if image_grid_appeared:
            logger.info("🔍 Image grid already detected in ШАГ 2, proceeding...")
        else:
            logger.info("🔍 Checking for image grid challenge...")
        
        # Try to find the AdvancedCaptcha (image task)
        grid_selectors = [
            "[class*='AdvancedCaptcha']",
            "[class*='Task-Grid']",
            "[class*='AdvancedCaptcha-Grid']",
            "[class*='Task'] img",
            "canvas[class*='captcha']",
        ]
        
        grid_found = False
        for selector in grid_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                if elements and any(el.is_displayed() for el in elements):
                    grid_found = True
                    logger.info(f"✅ Image grid found: {selector}")
                    break
            except:
                continue
        
        if not grid_found:
            # Wait a bit more — the grid might be loading
            logger.info("⏳ Waiting longer for image grid to appear...")
            time.sleep(5)
            
            # Save debug screenshot
            debug_ss = f"screenshots/captcha_wait_{int(time.time())}.png"
            driver.save_screenshot(debug_ss)
            
            for selector in grid_selectors:
                try:
                    elements = driver.find_elements(By.CSS_SELECTOR, selector)
                    if elements and any(el.is_displayed() for el in elements):
                        grid_found = True
                        logger.info(f"✅ Image grid appeared after wait: {selector}")
                        break
                except:
                    continue
        
        if not grid_found:
            # Check if maybe captcha passed while we waited
            current_check_url = driver.current_url.lower()
            current_check_path = current_check_url.split('?')[0]
            if 'checkcaptcha' in current_check_path:
                # Still on intermediate checkcaptcha page — wait for final redirect
                logger.info("⏳ Still on checkcaptcha, waiting for final redirect...")
                for redir_wait in range(15):
                    time.sleep(1)
                    current_check_url = driver.current_url.lower()
                    current_check_path = current_check_url.split('?')[0]
                    if 'checkcaptcha' not in current_check_path and 'showcaptcha' not in current_check_path:
                        logger.info(f"🎉 Final redirect completed: {driver.current_url[:100]}")
                        break
            if not detect_captcha_or_block(driver):
                logger.info("🎉 Captcha resolved while waiting!")
                return True
            
            # No image grid — checkbox-only captcha didn't pass (SmartCaptcha has ~2% success rate).
            # Strategy: Refresh page up to 3 times hoping Yandex switches to Kaleidoscope
            # (which we solve at 100% rate via PazlCaptchaV2).
            logger.info("⚠️ Checkbox captcha failed. Refreshing to try getting Kaleidoscope...")
            
            for refresh_attempt in range(1, 4):
                logger.info(f"🔄 Refresh attempt {refresh_attempt}/3 — looking for kaleidoscope...")
                try:
                    driver.refresh()
                except Exception as ref_err:
                    if 'Timed out' in str(ref_err) or 'timeout' in str(ref_err).lower():
                        logger.warning("⚠️ Timeout during refresh, waiting...")
                        time.sleep(10)
                    else:
                        logger.warning(f"⚠️ Refresh error: {ref_err}")
                
                time.sleep(random.uniform(3, 6))
                
                # Check what type of captcha appeared after refresh
                try:
                    page_src_refresh = driver.page_source.lower()
                except Exception:
                    page_src_refresh = ""
                
                # Check for kaleidoscope first (our best solver)
                is_kaleidoscope_now = any(kw in page_src_refresh for kw in [
                    'kaleidoscope', 'captchaslider', 'kaleidoscopecanvas'
                ])
                if is_kaleidoscope_now:
                    logger.info(f"🧩 Kaleidoscope appeared after refresh {refresh_attempt}! Solving via PazlCaptcha...")
                    return _solve_yandex_kaleidoscope_captcha(driver, screenshot_path, max_attempts=max_kaleidoscope_attempts)
                
                # Check for silhouette
                is_silhouette_now = any(kw in page_src_refresh for kw in [
                    'advancedcaptcha_silhouette', 'silhouette-container'
                ])
                if is_silhouette_now:
                    logger.info(f"🧩 Silhouette appeared after refresh {refresh_attempt}! Solving...")
                    return _solve_yandex_silhouette_captcha(driver, screenshot_path)
                
                # Check if no captcha at all (lucky!)
                if not detect_captcha_or_block(driver):
                    logger.info(f"🎉 Captcha disappeared after refresh {refresh_attempt}!")
                    return True
                
                # Still checkbox — try clicking one more time with human-like behavior
                checkbox_retry_clicked = False
                for selector in [".CheckboxCaptcha-Button", "[class*='CheckboxCaptcha'] button"]:
                    try:
                        elements = driver.find_elements(By.CSS_SELECTOR, selector)
                        for el in elements:
                            if el.is_displayed():
                                # Human-like: random offset, pause, click
                                ActionChains(driver)\
                                    .move_to_element_with_offset(el, random.randint(-3, 3), random.randint(-2, 2))\
                                    .pause(random.uniform(0.5, 1.5))\
                                    .click()\
                                    .perform()
                                checkbox_retry_clicked = True
                                logger.info(f"✅ Re-clicked checkbox on refresh {refresh_attempt}: {selector}")
                                break
                    except:
                        continue
                    if checkbox_retry_clicked:
                        break
                
                if checkbox_retry_clicked:
                    # Short wait for checkbox — if it works, it's fast
                    pre_url = driver.current_url
                    for wait_i in range(15):
                        time.sleep(1)
                        try:
                            new_url = driver.current_url
                            new_path = new_url.lower().split('?')[0]
                            if new_url != pre_url and 'showcaptcha' not in new_path and 'checkcaptcha' not in new_path:
                                logger.info(f"🎉 Redirected after re-click on refresh {refresh_attempt}! {new_url[:100]}")
                                time.sleep(2)
                                if not detect_captcha_or_block(driver):
                                    return True
                                break
                        except:
                            pass
                        # Check if kaleidoscope appeared after checkbox click
                        if wait_i > 0 and wait_i % 5 == 0:
                            try:
                                src_check = driver.page_source[:3000].lower()
                                if 'kaleidoscope' in src_check or 'captchaslider' in src_check:
                                    logger.info(f"🧩 Kaleidoscope appeared after checkbox click! Solving...")
                                    return _solve_yandex_kaleidoscope_captcha(driver, screenshot_path, max_attempts=max_kaleidoscope_attempts)
                            except:
                                pass
                        # Check for image grid
                        for sel in grid_selectors:
                            try:
                                elems = driver.find_elements(By.CSS_SELECTOR, sel)
                                if elems and any(e.is_displayed() for e in elems):
                                    grid_found = True
                                    logger.info(f"✅ Image grid appeared after re-click: {sel}")
                                    break
                            except:
                                continue
                        if grid_found:
                            break
                
                if grid_found:
                    break
            
            if not grid_found:
                if not detect_captcha_or_block(driver):
                    return True
                logger.warning("❌ Checkbox captcha failed after 3 refresh attempts — no kaleidoscope appeared")
                return False
        
        # ШАГ 4: Detect captcha subtype (Kaleidoscope / Silhouette / Image grid)
        try:
            page_src_check = driver.page_source.lower()
        except Exception as e:
            err_str = str(e)
            if 'Timed out' in err_str or 'timeout' in err_str.lower():
                logger.warning(f"⚠️ Renderer timeout reading page source for subtype detection — waiting for recovery...")
                time.sleep(10)
                try:
                    page_src_check = driver.page_source.lower()
                    logger.info("✅ Renderer recovered, got page source")
                except Exception:
                    logger.warning("⚠️ Renderer still unresponsive, using empty source (will default to SmartCaptcha)")
                    page_src_check = ""
            else:
                logger.warning(f"⚠️ Could not read page source for subtype detection: {e}")
                page_src_check = ""
        
        # 4a: Kaleidoscope (slider puzzle) → PazlCaptcha V1
        if ('kaleidoscope' in page_src_check or
            'captchaslider' in page_src_check or
            'kaleidoscopecanvas' in page_src_check or
            'captcha-slider' in page_src_check or
            '/ru/kaleidoscope' in page_src_check):
            logger.info("🧩 Kaleidoscope (slider puzzle) detected — using PazlCaptcha V1")
            return _solve_yandex_kaleidoscope_captcha(driver, screenshot_path, max_attempts=max_kaleidoscope_attempts)
        
        # 4b: Silhouette → SmartCaptcha
        if ('advancedcaptcha_silhouette' in page_src_check or
            'advancedcaptcha-silhouettetask' in page_src_check or
            'silhouette-container' in page_src_check or
            '/silhouette' in page_src_check):
            logger.info("🧩 Silhouette captcha detected after checkbox — switching to SmartCaptcha solver")
            return _solve_yandex_silhouette_captcha(driver, screenshot_path)
        
        # ШАГ 5: Image grid is visible — extract images for Capsola SmartCaptcha
        logger.info("📸 Extracting SmartCaptcha images for Capsola...")
        
        click_image_data = None
        task_image_data = None
        
        # Try to find task description element (shows what to click)
        task_desc_element = None
        task_desc_selectors = [
            "[class*='AdvancedCaptcha-TaskText']",
            "[class*='Task-Text']",
            ".AdvancedCaptcha-Task",
            "[class*='captcha-task']",
        ]
        for selector in task_desc_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for el in elements:
                    if el.is_displayed():
                        task_desc_element = el
                        logger.info(f"✅ Found task description: {selector}")
                        break
            except:
                continue
            if task_desc_element:
                break
        
        # Try to find grid element
        grid_element = None
        for selector in grid_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for el in elements:
                    if el.is_displayed() and el.size.get('height', 0) > 50:
                        grid_element = el
                        logger.info(f"✅ Found grid element: {selector} ({el.size})")
                        break
            except:
                continue
            if grid_element:
                break
        
        if task_desc_element and grid_element:
            logger.info("📸 Screenshotting individual SmartCaptcha elements")
            try:
                click_image_data = task_desc_element.screenshot_as_png
                task_image_data = grid_element.screenshot_as_png
            except Exception as e:
                logger.warning(f"Element screenshot failed: {e}")
        
        if not click_image_data or not task_image_data:
            # Fallback: try downloading captcha images from src attributes
            logger.info("📸 Trying to download captcha images from src...")
            try:
                img_elements = driver.find_elements(By.CSS_SELECTOR, "[class*='AdvancedCaptcha'] img, [class*='Task'] img")
                if len(img_elements) >= 2:
                    import requests as req
                    for i, img_el in enumerate(img_elements[:2]):
                        src = img_el.get_attribute('src')
                        if src and src.startswith('http'):
                            logger.info(f"📥 Downloading captcha image {i}: {src[:80]}")
                elif len(img_elements) == 1:
                    # Single image — might be the grid
                    src = img_elements[0].get_attribute('src')
                    if src:
                        logger.info(f"📥 Found single captcha image: {src[:80]}")
            except:
                pass
        
        if not click_image_data or not task_image_data:
            # Last resort: use full page screenshot approach
            logger.info("📸 Falling back to full screenshot split for Capsola")
            return _try_capsola_full_screenshot(driver, capsola, screenshot_path)
        
        # ШАГ 5: Send to Capsola
        return _send_to_capsola_and_click(driver, capsola, click_image_data, task_image_data, grid_element)
        
    except Exception as e:
        logger.error(f"❌ Error solving SmartCaptcha: {e}")
        import traceback
        traceback.print_exc()
        return False


def _solve_yandex_kaleidoscope_captcha(driver, screenshot_path: str, max_attempts: int = 7) -> bool:
    """Solve Yandex Kaleidoscope (slider puzzle) captcha using Capsola PazlCaptcha API.
    
    This captcha shows a scrambled 5x5 image grid with a slider (0 to ~42 steps).
    Moving the slider applies tile swaps. The correct position assembles the puzzle.
    
    Strategy:
    1. Get step from Capsola (V1 HTML first, then V2 image+permutations)
    2. Set rep=step directly via JS and submit the form (no physical drag)
    3. If failed, try step+1 on next captcha (Capsola can be off by 1)
    4. Retry up to MAX_ATTEMPTS times
    """
    from app.config import settings
    from core.capsola_solver import create_capsola_solver
    
    MAX_ATTEMPTS = max(1, int(max_attempts))
    # No adjustments — each attempt gets a DIFFERENT captcha image,
    # so adjusting based on a previous captcha's answer is meaningless.
    # Always submit the raw Capsola answer for each new captcha.
    STEP_ADJUSTMENTS = [0, 0, 0, 0, 0, 0, 0]
    
    try:
        capsola = create_capsola_solver(settings.capsola_api_key)
        
        # Set moderate timeouts for captcha solving (120s, not 300).
        # IMPORTANT: caller (handle_yandex_protection) MUST restore timeout to 60 after return.
        try:
            driver.set_page_load_timeout(120)
            driver.set_script_timeout(120)
        except Exception as e:
            logger.warning(f"Could not set timeouts: {e}")
        
        for attempt in range(1, MAX_ATTEMPTS + 1):
            logger.info(f"🧩 Kaleidoscope attempt {attempt}/{MAX_ATTEMPTS}")
            
            # Save debug screenshot
            try:
                debug_ss = f"screenshots/kaleidoscope_debug_{int(time.time())}.png"
                driver.save_screenshot(debug_ss)
            except:
                pass
            
            # Dump SSR_DATA for diagnostics (with timeout recovery)
            try:
                ssr_data = driver.execute_script("return window.__SSR_DATA__ || null;")
            except Exception as _ssr_err:
                _ssr_err_str = str(_ssr_err)
                if 'Timed out' in _ssr_err_str or 'timeout' in _ssr_err_str.lower():
                    logger.warning(f"⚠️ Renderer timeout reading SSR_DATA on attempt {attempt}: {_ssr_err_str[:150]}")
                    # Wait for renderer to recover
                    time.sleep(10)
                    try:
                        ssr_data = driver.execute_script("return window.__SSR_DATA__ || null;")
                    except Exception:
                        logger.error(f"❌ Renderer still dead after retry — skipping attempt {attempt}")
                        if attempt < MAX_ATTEMPTS:
                            try:
                                driver.refresh()
                            except Exception:
                                pass
                            time.sleep(random.uniform(3, 6))
                            continue
                        return False
                else:
                    raise
            if ssr_data:
                task_str = ssr_data.get('task', '')
                image_src = ssr_data.get('imageSrc', '')
                logger.info(f"📋 SSR_DATA: imageSrc={'yes' if image_src else 'no'}, task_len={len(task_str) if task_str else 0}")
            else:
                logger.warning("⚠️ No __SSR_DATA__ found!")
            
            step = None
            
            # === Get step from Capsola ===
            # Try V2 (image + permutations) first — more accurate than V1
            v2_step = _get_kaleidoscope_v2_step(driver, capsola)
            if v2_step is not None:
                step = v2_step
                logger.info(f"✅ Got step from V2: {step}")
            
            # V1 (HTML) fallback disabled — Capsola returns CAPCHA_NOT_AVAILABLE for V1.
            # All PazlCaptcha solving goes through V2 (image + permutations).
            if step is None:
                logger.warning(f"⚠️ V2 did not return a step — skipping V1 (not supported by Capsola)")
            
            if step is None:
                logger.error(f"❌ No valid step from Capsola on attempt {attempt}")
                if attempt < MAX_ATTEMPTS:
                    logger.info("🔄 Refreshing page for new captcha...")
                    driver.refresh()
                    time.sleep(random.uniform(3, 6))
                    continue
                return False
            
            # Apply step adjustment for this attempt
            adjustment = STEP_ADJUSTMENTS[(attempt - 1) % len(STEP_ADJUSTMENTS)]
            adjusted_step = step + adjustment
            logger.info(f"🔢 Capsola step: {step}, adjustment: {adjustment:+d}, submitting: {adjusted_step}")
            
            # Submit captcha with the adjusted step
            solved = _move_kaleidoscope_slider(driver, adjusted_step)
            if solved:
                logger.info(f"🎉 Kaleidoscope solved on attempt {attempt} (step={adjusted_step}, adj={adjustment:+d})!")
                return True
            
            logger.warning(f"⚠️ Kaleidoscope attempt {attempt} failed (step={adjusted_step})")
            
            if attempt < MAX_ATTEMPTS:
                # After failed submission, Yandex redirects to new captcha page
                time.sleep(random.uniform(1, 2))
                
                try:
                    if not detect_captcha_or_block(driver):
                        logger.info("🎉 Captcha disappeared after submit — solved!")
                        return True
                except Exception as e:
                    err_str = str(e)
                    if 'Timed out' in err_str or 'timeout' in err_str.lower():
                        logger.warning(f"⚠️ Renderer timeout after kaleidoscope submit: {err_str[:200]}")
                        time.sleep(10)
                        try:
                            if not detect_captcha_or_block(driver):
                                logger.info("🎉 Captcha solved (after timeout recovery)!")
                                return True
                        except Exception:
                            pass
                    else:
                        logger.warning(f"⚠️ Error checking captcha: {err_str[:200]}")
                
                try:
                    page_src = driver.page_source.lower()
                    if 'kaleidoscope' not in page_src and 'captcha' not in page_src:
                        logger.info("🔄 Different page after failed attempt — checking...")
                        if not detect_captcha_or_block(driver):
                            return True
                        return False
                except Exception as e:
                    logger.warning(f"⚠️ Could not check page source: {e}")
                    return False
                
                # Stay on current page — new captcha is already loaded (status=failed redirects to new captcha)
                logger.info("🔄 New captcha loaded, retrying...")
                time.sleep(random.uniform(1, 3))
        
        logger.error(f"❌ All {MAX_ATTEMPTS} kaleidoscope attempts failed")
        return False
        
    except Exception as e:
        err_str = str(e)
        if 'Timed out' in err_str or 'timeout' in err_str.lower():
            logger.warning(f"⚠️ Renderer timeout in Kaleidoscope solver: {err_str[:200]}")
            # Try to recover the browser for the caller
            try:
                time.sleep(5)
                _ = driver.current_url  # Test if browser is responsive
                logger.info("✅ Browser recovered after Kaleidoscope timeout")
            except Exception:
                logger.error("💀 Browser unresponsive after Kaleidoscope timeout")
        else:
            logger.error(f"❌ Error solving Kaleidoscope: {e}")
            import traceback
            traceback.print_exc()
        return False


def _get_kaleidoscope_v2_step(driver, capsola) -> int:
    """Get kaleidoscope step via Capsola PazlCaptcha V2 (image + permutations).
    
    IMPORTANT: Downloads the captcha image THROUGH THE BROWSER (via fetch + proxy)
    to ensure we get the exact same image Yandex tied to this session/IP.
    Using requests.get directly would bypass the proxy and potentially get a different image.
    """
    try:
        # Set async script timeout for fetch operations
        try:
            driver.set_script_timeout(30)
        except Exception:
            pass
        
        try:
            ssr_data = driver.execute_script("return window.__SSR_DATA__ || null;")
        except Exception as _ssr_err:
            _ssr_str = str(_ssr_err)
            if 'Timed out' in _ssr_str or 'timeout' in _ssr_str.lower():
                logger.warning(f"⚠️ [V2] Renderer timeout reading SSR_DATA: {_ssr_str[:150]}")
                time.sleep(5)
                try:
                    ssr_data = driver.execute_script("return window.__SSR_DATA__ || null;")
                except Exception:
                    logger.error("❌ [V2] Renderer still dead after retry")
                    return None
            else:
                raise
        if not ssr_data:
            logger.error("❌ [V2] No __SSR_DATA__ found")
            return None
        
        image_src = ssr_data.get('imageSrc', '')
        task_str = ssr_data.get('task', '')
        
        if not image_src or not task_str:
            logger.error(f"❌ [V2] Missing imageSrc or task in SSR_DATA")
            return None
        
        logger.info(f"📋 [V2] imageSrc URL: {image_src[:120]}")
        
        import json as json_mod
        try:
            permutations = json_mod.loads(task_str) if isinstance(task_str, str) else task_str
        except:
            logger.error(f"❌ [V2] Cannot parse task array: {task_str[:100]}")
            return None
        
        # Download image THROUGH THE BROWSER to use the same proxy/IP/session
        # This is critical — requests.get without proxy bypasses the proxy and gets a different image
        image_b64 = None
        
        try:
            # Set generous script timeout for image download via slow proxy
            try:
                driver.set_script_timeout(60)
            except Exception:
                pass
            
            # Method 1: Try extracting from existing <img> element on the page (most reliable)
            image_b64 = driver.execute_async_script("""
                var callback = arguments[arguments.length - 1];
                var url = arguments[0];
                
                // Method A: Find existing <img> with this src already loaded
                var imgs = document.querySelectorAll('img');
                for (var i = 0; i < imgs.length; i++) {
                    if (imgs[i].src && imgs[i].src.indexOf('captchaimage') !== -1 && imgs[i].complete && imgs[i].naturalWidth > 0) {
                        try {
                            var canvas = document.createElement('canvas');
                            canvas.width = imgs[i].naturalWidth;
                            canvas.height = imgs[i].naturalHeight;
                            canvas.getContext('2d').drawImage(imgs[i], 0, 0);
                            var dataUrl = canvas.toDataURL('image/png');
                            callback({data: dataUrl.split(',')[1], size: imgs[i].naturalWidth * imgs[i].naturalHeight, method: 'existing_img'});
                            return;
                        } catch(e) { /* tainted canvas, try next method */ }
                    }
                }
                
                // Method B: XMLHttpRequest (goes through browser proxy, unlike fetch which may fail)
                var xhr = new XMLHttpRequest();
                xhr.open('GET', url, true);
                xhr.responseType = 'blob';
                xhr.withCredentials = true;
                xhr.onload = function() {
                    if (xhr.status === 200) {
                        var reader = new FileReader();
                        reader.onloadend = function() {
                            callback({data: reader.result.split(',')[1], size: xhr.response.size, method: 'xhr'});
                        };
                        reader.readAsDataURL(xhr.response);
                    } else {
                        callback({error: 'XHR HTTP ' + xhr.status});
                    }
                };
                xhr.onerror = function() {
                    // Method C: fetch as last browser attempt
                    fetch(url, {credentials: 'include'})
                        .then(function(resp) {
                            if (!resp.ok) { callback({error: 'fetch HTTP ' + resp.status}); return; }
                            return resp.blob();
                        })
                        .then(function(blob) {
                            if (!blob) return;
                            var reader2 = new FileReader();
                            reader2.onloadend = function() {
                                callback({data: reader2.result.split(',')[1], size: blob.size, method: 'fetch'});
                            };
                            reader2.readAsDataURL(blob);
                        })
                        .catch(function(e) {
                            callback({error: 'all browser methods failed: ' + e.message});
                        });
                };
                xhr.send();
            """, image_src)
        except Exception as fetch_err:
            fetch_err_str = str(fetch_err)
            if 'Timed out' in fetch_err_str or 'timeout' in fetch_err_str.lower():
                logger.warning(f"⚠️ [V2] Renderer timeout during image download — falling back to requests: {fetch_err_str[:150]}")
            else:
                logger.warning(f"⚠️ [V2] Browser image download failed: {fetch_err}")
            image_b64 = None
        
        if image_b64 and not image_b64.get('error') and image_b64.get('data'):
            import base64
            image_data = base64.b64decode(image_b64['data'])
            dl_method = image_b64.get('method', 'unknown')
            logger.info(f"✅ [V2] Image downloaded via browser ({dl_method}): {len(image_data)} bytes")
        else:
            logger.warning(f"⚠️ [V2] All browser download methods failed: {image_b64}, falling back to requests.get with proxy")
            import requests as req
            cookies = {c['name']: c['value'] for c in driver.get_cookies()}
            ua = driver.execute_script("return navigator.userAgent")
            referer = driver.current_url
            
            # Try to get proxy info from the browser to use same IP
            proxy_url = None
            try:
                # Check Chrome's proxy settings via CDP
                # The proxy extension or --proxy-server flag routes through local proxy
                # Try to find the local proxy port
                chrome_args = driver.execute_script("return navigator.userAgent")  # just to verify driver is alive
                # Look for proxy in Chrome capabilities
                caps = driver.capabilities
                proxy_info = caps.get('proxy', {})
                if proxy_info:
                    logger.info(f"📋 [V2] Browser proxy info: {proxy_info}")
            except Exception:
                pass
            
            # First try with proxy from proxy_ext if available
            downloaded = False
            try:
                import glob
                proxy_ext_bg = glob.glob('/tmp/proxy_ext_*/background.js')
                if proxy_ext_bg:
                    with open(proxy_ext_bg[-1], 'r') as f:
                        bg_content = f.read()
                    # Extract proxy details from background.js
                    import re as re_mod
                    host_match = re_mod.search(r'host:\s*["\']([^"\']+)', bg_content)
                    port_match = re_mod.search(r'port:\s*["\']?(\d+)', bg_content)
                    user_match = re_mod.search(r'username:\s*["\']([^"\']+)', bg_content)
                    pass_match = re_mod.search(r'password:\s*["\']([^"\']+)', bg_content)
                    if host_match and port_match:
                        p_host = host_match.group(1)
                        p_port = port_match.group(1)
                        p_user = user_match.group(1) if user_match else ''
                        p_pass = pass_match.group(1) if pass_match else ''
                        if p_user and p_pass:
                            proxy_url = f"http://{p_user}:{p_pass}@{p_host}:{p_port}"
                        else:
                            proxy_url = f"http://{p_host}:{p_port}"
                        logger.info(f"📋 [V2] Found proxy from ext: {p_host}:{p_port}")
                        resp = req.get(image_src, cookies=cookies, timeout=20,
                                       proxies={'http': proxy_url, 'https': proxy_url},
                                       headers={'User-Agent': ua, 'Referer': referer},
                                       verify=False)
                        if resp.status_code == 200 and len(resp.content) > 100:
                            image_data = resp.content
                            downloaded = True
                            logger.info(f"✅ [V2] Image downloaded via requests+proxy: {len(image_data)} bytes")
            except Exception as proxy_err:
                logger.warning(f"⚠️ [V2] Proxy requests.get failed: {proxy_err}")
            
            if not downloaded:
                # Last resort: requests.get without proxy (LIKELY WRONG IMAGE but better than nothing)
                try:
                    resp = req.get(image_src, cookies=cookies, timeout=15, headers={
                        'User-Agent': ua, 'Referer': referer
                    })
                    if resp.status_code != 200 or len(resp.content) < 100:
                        logger.error(f"❌ [V2] All download methods failed: HTTP {resp.status_code}")
                        return None
                    image_data = resp.content
                    logger.warning(f"⚠️ [V2] Image downloaded WITHOUT proxy (may be wrong image): {len(image_data)} bytes")
                except Exception as req_err:
                    logger.error(f"❌ [V2] All download methods failed: {req_err}")
                    return None
        
        logger.info(f"📤 [V2] Sending image={len(image_data)}b, permutations={len(permutations)} items")
        
        result = capsola.solve_pazl_captcha_v2(image_data, permutations, max_wait=120)
        
        if not result or result.get('status') != 1:
            logger.error(f"❌ [V2] PazlCaptcha V2 failed: {result}")
            return None
        
        answer = result.get('response', '')
        logger.info(f"✅ [V2] Capsola PazlCaptcha V2 answer: {answer}")
        
        try:
            return int(str(answer).strip())
        except (ValueError, TypeError):
            logger.error(f"❌ [V2] Cannot parse step: {answer}")
            return None
        
    except Exception as e:
        logger.error(f"❌ [V2] Error: {e}")
        import traceback
        traceback.print_exc()
        return None


def _move_kaleidoscope_slider(driver, step: int) -> bool:
    """Set the kaleidoscope rep value and submit the form directly via JavaScript.
    
    Instead of physically dragging the slider (which fails to update React's internal state
    and causes premature form.submit() with rep=0 on mouseup), we:
    1. Wait for PoW (pdata) and fingerprint (rdata) fields to be computed
    2. Set the hidden 'rep' input value directly to the target step
    3. Submit the form via JS
    4. Wait for redirect to determine success/failure
    """
    try:
        # Get slider max value from __SSR_DATA__ task array (with timeout recovery)
        try:
            ssr_data = driver.execute_script("return window.__SSR_DATA__ || null;")
        except Exception as _ssr_err:
            _ssr_str = str(_ssr_err)
            if 'Timed out' in _ssr_str or 'timeout' in _ssr_str.lower():
                logger.warning(f"⚠️ Renderer timeout reading SSR_DATA in slider: {_ssr_str[:150]}")
                time.sleep(10)
                try:
                    ssr_data = driver.execute_script("return window.__SSR_DATA__ || null;")
                except Exception:
                    logger.error("❌ Renderer still dead in slider — cannot submit")
                    return False
            else:
                raise
        max_step = 42  # default
        if ssr_data:
            task_str = ssr_data.get('task', '')
            try:
                import json as json_mod
                task_arr = json_mod.loads(task_str) if isinstance(task_str, str) else task_str
                if isinstance(task_arr, list):
                    max_step = len(task_arr) // 2
                    logger.info(f"📋 Task array length: {len(task_arr)}, max_step: {max_step}")
            except:
                pass
        
        # Clamp step to valid range
        step = max(0, min(step, max_step))
        logger.info(f"🎯 Setting rep={step} (max={max_step})")
        
        # Wait for PoW (pdata) AND fingerprint fields to be computed by page JavaScript
        # Increased from 30s to 90s — with 12 concurrent Chrome workers, PoW needs more CPU time
        fields_ready = False
        for wait_i in range(90):
            try:
                field_status = driver.execute_script("""
                    var pdata = document.querySelector('input[name="pdata"]');
                    var rdata = document.querySelector('input[name="rdata"]');
                    var picasso = document.querySelector('input[name="picasso"]');
                    return {
                        pdata: (pdata && pdata.value) ? pdata.value.length : 0,
                        rdata: (rdata && rdata.value) ? rdata.value.length : 0,
                        picasso: (picasso && picasso.value) ? picasso.value.length : 0
                    };
                """)
            except Exception as _fs_err:
                _fs_str = str(_fs_err)
                if 'Timed out' in _fs_str or 'timeout' in _fs_str.lower():
                    logger.warning(f"⚠️ Renderer timeout checking form fields at {wait_i}s — waiting for recovery...")
                    time.sleep(10)
                    continue
                raise
            pdata_len = field_status.get('pdata', 0) if field_status else 0
            rdata_len = field_status.get('rdata', 0) if field_status else 0
            picasso_len = field_status.get('picasso', 0) if field_status else 0
            
            # Require PoW plus at least one fingerprint field (rdata or picasso)
            if pdata_len > 100 and (rdata_len > 100 or picasso_len > 100):
                logger.info(f"✅ Form fields ready (pdata: {pdata_len}, rdata: {rdata_len}, picasso: {picasso_len})")
                fields_ready = True
                break
            elif pdata_len > 200 and (rdata_len > 0 or picasso_len > 0):
                # Partial but likely usable state
                if wait_i > 20:
                    logger.info(f"✅ PoW partially ready (pdata: {pdata_len}, rdata: {rdata_len}, picasso: {picasso_len}), proceeding")
                    fields_ready = True
                    break
            time.sleep(1)
            if wait_i % 5 == 4:
                logger.info(f"⏳ Waiting for form fields... ({wait_i + 1}s, pdata={pdata_len}, rdata={rdata_len}, picasso={picasso_len})")
        
        if not fields_ready:
            logger.warning("⚠️ Form fields not fully computed after 90s, proceeding anyway")
        
        # Also check that rdata and picasso are present (with timeout recovery)
        try:
            form_status = driver.execute_script("""
                var form = document.getElementById('advanced-captcha-form');
                if (!form) {
                    var forms = document.querySelectorAll('form');
                    for (var i = 0; i < forms.length; i++) {
                        if (forms[i].querySelector('input[name="rep"]')) {
                            form = forms[i];
                            break;
                        }
                    }
                }
                if (!form) return {found: false};
                return {
                    found: true,
                    repExists: !!form.querySelector('input[name="rep"]'),
                    rdataLen: (form.querySelector('input[name="rdata"]')?.value || '').length,
                    pdataLen: (form.querySelector('input[name="pdata"]')?.value || '').length,
                    picassoLen: (form.querySelector('input[name="picasso"]')?.value || '').length,
                    tdataLen: (form.querySelector('input[name="tdata"]')?.value || '').length,
                    formAction: (form.action || '').substring(0, 80)
                };
            """)
        except Exception as _form_err:
            _form_str = str(_form_err)
            if 'Timed out' in _form_str or 'timeout' in _form_str.lower():
                logger.warning(f"⚠️ Renderer timeout reading form status — waiting for recovery...")
                time.sleep(10)
                try:
                    form_status = driver.execute_script("""
                        var form = document.getElementById('advanced-captcha-form') || 
                                   document.querySelector('form');
                        if (!form) return {found: false};
                        return {found: true, repExists: !!form.querySelector('input[name="rep"]'),
                                rdataLen: 999, pdataLen: 999, picassoLen: 0, tdataLen: 0,
                                formAction: (form.action || '').substring(0, 80)};
                    """)
                except Exception:
                    logger.error("❌ Renderer still dead — cannot submit form")
                    return False
            else:
                raise
        logger.info(f"📋 Form status: {form_status}")
        
        if not form_status or not form_status.get('found'):
            logger.error("❌ Captcha form not found!")
            return False

        if form_status.get('pdataLen', 0) < 100 or (
            form_status.get('rdataLen', 0) == 0 and form_status.get('picassoLen', 0) == 0
        ):
            logger.warning(
                f"⚠️ Captcha fields not ready for submit (pdata={form_status.get('pdataLen', 0)}, "
                f"rdata={form_status.get('rdataLen', 0)}, picasso={form_status.get('picassoLen', 0)})"
            )
            return False
        
        # Small random delay to simulate "thinking" before submission
        time.sleep(random.uniform(1.0, 3.0))
        
        # Set rep value and submit the form in one atomic JS call
        try:
            pre_url = driver.current_url
        except Exception as _pu_err:
            if 'Timed out' in str(_pu_err) or 'timeout' in str(_pu_err).lower():
                logger.warning("⚠️ Renderer timeout getting pre_url — waiting...")
                time.sleep(10)
                try:
                    pre_url = driver.current_url
                except Exception:
                    logger.error("❌ Renderer dead — cannot submit")
                    return False
            else:
                raise
        try:
            submit_result = driver.execute_script(f"""
            try {{
                var form = document.getElementById('advanced-captcha-form');
                if (!form) {{
                    var forms = document.querySelectorAll('form');
                    for (var i = 0; i < forms.length; i++) {{
                        if (forms[i].querySelector('input[name="rep"]')) {{
                            form = forms[i];
                            break;
                        }}
                    }}
                }}
                if (!form) return {{success: false, error: 'Form not found'}};
                
                // Set rep value — remove readonly first
                var repInput = form.querySelector('input[name="rep"]');
                if (!repInput) return {{success: false, error: 'Rep input not found'}};
                
                repInput.removeAttribute('readonly');
                repInput.value = '{step}';
                
                // Also update aria attributes on slider for consistency
                var slider = document.querySelector('[role="slider"], #captcha-slider, .Thumb');
                if (slider) {{
                    slider.setAttribute('aria-valuenow', '{step}');
                    slider.setAttribute('aria-valuetext', '{step}');
                }}
                
                // Verify rep value was set
                var finalRep = repInput.value;
                
                // Submit the form
                form.submit();
                
                return {{
                    success: true,
                    repValue: finalRep
                }};
            }} catch(e) {{
                return {{success: false, error: e.message}};
            }}
        """)
        except Exception as _submit_err:
            _submit_str = str(_submit_err)
            if 'Timed out' in _submit_str or 'timeout' in _submit_str.lower():
                logger.warning(f"⚠️ Renderer timeout during form.submit() — form may have been submitted anyway")
                # Wait and check if page changed (form.submit() triggers navigation)
                time.sleep(15)
                try:
                    new_url = driver.current_url
                    if new_url != pre_url and 'status=failed' not in new_url.lower():
                        if not detect_captcha_or_block(driver):
                            logger.info("🎉 Form was submitted despite timeout — captcha solved!")
                            return True
                except Exception:
                    pass
                return False
            else:
                raise
        
        logger.info(f"📨 Submit result: {submit_result}")
        
        if not submit_result or not submit_result.get('success'):
            logger.error(f"❌ Form submission failed: {submit_result}")
            return False
        
        # Wait for page navigation (form POST → redirect)
        # Use generous timeout — proxy navigation can take 60+ seconds,
        # which causes "Timed out receiving message from renderer" if we
        # call driver.current_url while Chrome is still loading.
        for wait_i in range(30):
            time.sleep(2)
            try:
                new_url = driver.current_url
                if new_url != pre_url:
                    # Page changed — check result
                    if 'status=failed' in new_url.lower():
                        logger.warning(f"❌ Yandex returned status=failed for step={step}")
                        return False
                    
                    if 'showcaptcha' not in new_url.lower().split('?')[0] and 'checkcaptcha' not in new_url.lower().split('?')[0]:
                        # Redirected away from captcha — success!
                        logger.info(f"🎉 Redirected to: {new_url[:100]}")
                        time.sleep(1)
                        if not detect_captcha_or_block(driver):
                            return True
                        # New captcha appeared at destination — still a failure for this attempt
                        logger.warning("⚠️ New captcha at redirect destination")
                        return False
                    
                    if 'checkcaptcha' in new_url.lower().split('?')[0]:
                        # Still processing
                        logger.info(f"⏳ Processing... ({wait_i * 2}s)")
                        continue
                    
                    # Other showcaptcha URL without status=failed — new captcha
                    logger.info(f"⏳ Redirected to new captcha: {new_url[:100]}")
                    time.sleep(2)
                    return False
            except TimeoutException:
                logger.warning(f"⚠️ Renderer timeout at {wait_i * 2}s after submit — page still loading")
                continue
            except Exception as e:
                err_str = str(e)
                if 'Timed out' in err_str or 'timeout' in err_str.lower():
                    logger.warning(f"⚠️ Timeout at {wait_i * 2}s: {err_str[:200]}")
                    continue
                logger.debug(f"URL check error: {e}")
                pass
        
        # Timeout — check final state
        if not detect_captcha_or_block(driver):
            logger.info("🎉 Captcha gone after wait — solved!")
            return True
        
        logger.warning(f"⚠️ Still on captcha after 20s wait")
        return False
        
    except Exception as e:
        logger.error(f"❌ Error in slider submission: {e}")
        import traceback
        traceback.print_exc()
        return False


def _solve_yandex_silhouette_captcha(driver, screenshot_path: str) -> bool:
    """Solve Yandex Silhouette/PazlCaptcha using Capsola PazlCaptcha V1 API.
    
    This captcha type shows an image with silhouettes that need to be clicked in order.
    It is actually a SmartCaptcha variant — two images (click area + task icons) and 
    coordinate-based response, NOT a PazlCaptcha (puzzle permutation).
    
    Flow:
    1. Extract silhouette image (click area) and task icons image
    2. Download actual images from src URLs (better quality than screenshots)
    3. Send to Capsola SmartCaptcha API
    4. Parse coordinate result and click on the image
    5. Submit the form
    """
    from app.config import settings
    from core.capsola_solver import create_capsola_solver
    import requests as req
    
    try:
        capsola = create_capsola_solver(settings.capsola_api_key)
        
        # Save debug screenshot
        try:
            debug_ss = f"screenshots/silhouette_debug_{int(time.time())}.png"
            driver.save_screenshot(debug_ss)
            debug_html = debug_ss.replace('.png', '.html')
            with open(debug_html, 'w', encoding='utf-8') as f:
                f.write(driver.page_source)
            logger.info(f"📄 Silhouette debug saved: {debug_html}")
        except:
            pass
        
        # ШАГ 1: Extract the two images — silhouette (click area) and task icons
        click_image_data = None  # main silhouette image 
        task_image_data = None   # task icons strip
        
        # --- Try downloading images from src URLs first (best quality) ---
        try:
            # Get image URLs from __SSR_DATA__ or from img elements
            image_src = None
            task_image_src = None
            
            # Try __SSR_DATA__ first
            ssr_data = driver.execute_script("return window.__SSR_DATA__ || null;")
            if ssr_data and isinstance(ssr_data, dict):
                image_src = ssr_data.get('imageSrc')
                task_image_src = ssr_data.get('taskImageSrc')
                logger.info(f"📋 SSR_DATA: imageSrc={'yes' if image_src else 'no'}, taskImageSrc={'yes' if task_image_src else 'no'}")
            
            # Fallback: get from img elements
            if not image_src:
                try:
                    el = driver.find_element(By.CSS_SELECTOR, "[data-testid='silhouette-container'] img, .AdvancedCaptcha-ImageWrapper img")
                    image_src = el.get_attribute('src')
                except:
                    pass
            
            if not task_image_src:
                try:
                    el = driver.find_element(By.CSS_SELECTOR, ".AdvancedCaptcha-SilhouetteTask img.TaskImage, .AdvancedCaptcha-SilhouetteTask img")
                    task_image_src = el.get_attribute('src')
                except:
                    pass
            
            # Download images
            if image_src and image_src.startswith('http'):
                logger.info(f"📥 Downloading silhouette image from URL...")
                cookies = {c['name']: c['value'] for c in driver.get_cookies()}
                resp = req.get(image_src, cookies=cookies, timeout=15, headers={
                    'User-Agent': driver.execute_script("return navigator.userAgent"),
                    'Referer': driver.current_url
                })
                if resp.status_code == 200 and len(resp.content) > 100:
                    click_image_data = resp.content
                    logger.info(f"✅ Downloaded silhouette image: {len(click_image_data)} bytes")
            
            if task_image_src and task_image_src.startswith('http'):
                logger.info(f"📥 Downloading task icons image from URL...")
                cookies = {c['name']: c['value'] for c in driver.get_cookies()}
                resp = req.get(task_image_src, cookies=cookies, timeout=15, headers={
                    'User-Agent': driver.execute_script("return navigator.userAgent"),
                    'Referer': driver.current_url
                })
                if resp.status_code == 200 and len(resp.content) > 100:
                    task_image_data = resp.content
                    logger.info(f"✅ Downloaded task icons image: {len(task_image_data)} bytes")
        except Exception as e:
            logger.warning(f"⚠️ Failed to download images from URLs: {e}")
        
        # --- Fallback: screenshot elements ---
        if not click_image_data:
            try:
                for sel in ["[data-testid='silhouette-container'] img", ".AdvancedCaptcha-ImageWrapper img",
                            ".AdvancedCaptcha-View img[alt='Image challenge']"]:
                    try:
                        el = driver.find_element(By.CSS_SELECTOR, sel)
                        if el.is_displayed():
                            click_image_data = el.screenshot_as_png
                            logger.info(f"📸 Screenshot silhouette image via {sel}: {len(click_image_data)} bytes")
                            break
                    except:
                        continue
            except:
                pass
        
        if not task_image_data:
            try:
                for sel in [".AdvancedCaptcha-SilhouetteTask img.TaskImage", 
                            ".AdvancedCaptcha-SilhouetteTask img",
                            ".AdvancedCaptcha img.TaskImage"]:
                    try:
                        el = driver.find_element(By.CSS_SELECTOR, sel)
                        if el.is_displayed():
                            task_image_data = el.screenshot_as_png
                            logger.info(f"📸 Screenshot task icons via {sel}: {len(task_image_data)} bytes")
                            break
                    except:
                        continue
            except:
                pass
        
        if not click_image_data or not task_image_data:
            logger.error(f"❌ Could not extract silhouette images (click={'yes' if click_image_data else 'no'}, task={'yes' if task_image_data else 'no'})")
            # Fallback to full screenshot approach
            return _try_capsola_full_screenshot(driver, capsola, screenshot_path)
        
        # ШАГ 2: Solve with retries (up to 3 attempts)
        for solve_attempt in range(1, 4):
            # For SmartCaptcha: click = main image (silhouette), task = task description icons
            logger.info(f"🔄 [{solve_attempt}/3] Sending Silhouette as SmartCaptcha to Capsola (click={len(click_image_data)}b, task={len(task_image_data)}b)...")
            result = capsola.solve_smart_captcha(click_image_data, task_image_data, max_wait=120)
            
            if not result or result.get('status') != 1:
                logger.warning(f"⚠️ [{solve_attempt}/3] SmartCaptcha failed for silhouette: {result}")
                if solve_attempt < 3:
                    time.sleep(2)
                    continue
                # Final attempt failed → fallback to full screenshot
                return _try_capsola_full_screenshot(driver, capsola, screenshot_path)
            
            answer = result.get('response', '')
            logger.info(f"✅ [{solve_attempt}/3] SmartCaptcha silhouette answer: {answer}")
            
            # ШАГ 3: Apply coordinate-based answer
            solved = _apply_silhouette_answer(driver, answer, click_image_data)
            if solved:
                return True
            
            if solve_attempt < 3:
                logger.info(f"🔄 [{solve_attempt}/3] Silhouette answer didn't work, retrying...")
                # Re-extract images for next attempt (captcha may have refreshed)
                time.sleep(2)
                try:
                    for sel in ["[data-testid='silhouette-container'] img", ".AdvancedCaptcha-ImageWrapper img"]:
                        try:
                            el = driver.find_element(By.CSS_SELECTOR, sel)
                            if el.is_displayed():
                                new_src = el.get_attribute('src')
                                if new_src and new_src.startswith('http'):
                                    cookies = {c['name']: c['value'] for c in driver.get_cookies()}
                                    resp = req.get(new_src, cookies=cookies, timeout=15, headers={
                                        'User-Agent': driver.execute_script("return navigator.userAgent"),
                                        'Referer': driver.current_url
                                    })
                                    if resp.status_code == 200 and len(resp.content) > 100:
                                        click_image_data = resp.content
                                        logger.info(f"📥 Re-downloaded silhouette image: {len(click_image_data)} bytes")
                                break
                        except:
                            continue
                    for sel in [".AdvancedCaptcha-SilhouetteTask img.TaskImage", ".AdvancedCaptcha-SilhouetteTask img"]:
                        try:
                            el = driver.find_element(By.CSS_SELECTOR, sel)
                            if el.is_displayed():
                                new_src = el.get_attribute('src')
                                if new_src and new_src.startswith('http'):
                                    cookies = {c['name']: c['value'] for c in driver.get_cookies()}
                                    resp = req.get(new_src, cookies=cookies, timeout=15, headers={
                                        'User-Agent': driver.execute_script("return navigator.userAgent"),
                                        'Referer': driver.current_url
                                    })
                                    if resp.status_code == 200 and len(resp.content) > 100:
                                        task_image_data = resp.content
                                        logger.info(f"📥 Re-downloaded task image: {len(task_image_data)} bytes")
                                break
                        except:
                            continue
                except Exception as re_err:
                    logger.warning(f"⚠️ Could not re-extract images: {re_err}")
        
        return False
        
    except Exception as e:
        logger.error(f"❌ Error solving Silhouette captcha: {e}")
        import traceback
        traceback.print_exc()
        return False


def _try_pazl_captcha_v2(driver, capsola) -> Optional[Dict]:
    """Try solving with PazlCaptcha V2 (image + permutations).
    
    Extracts the captcha image and permutation data from the page,
    sends to Capsola PazlCaptcha V2 API.
    """
    try:
        # Find the main captcha image
        image_element = None
        image_selectors = [
            "[data-testid='silhouette-container'] img",
            ".AdvancedCaptcha-ImageWrapper img",
            ".AdvancedCaptcha_silhouette img[alt='Image challenge']",
            ".AdvancedCaptcha img[alt='Image challenge']",
        ]
        
        for selector in image_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for el in elements:
                    if el.is_displayed():
                        image_element = el
                        logger.info(f"✅ Found silhouette image: {selector}")
                        break
            except:
                continue
            if image_element:
                break
        
        if not image_element:
            logger.warning("⚠️ Could not find silhouette image element")
            return None
        
        # Get image as screenshot
        image_data = image_element.screenshot_as_png
        logger.info(f"📸 Captured silhouette image: {len(image_data)} bytes")
        
        # Try to extract permutations from SSR data  
        permutations = []
        try:
            ssr_data = driver.execute_script("return window.__SSR_DATA__ || null;")
            if ssr_data:
                logger.info(f"📋 SSR data keys: {list(ssr_data.keys()) if isinstance(ssr_data, dict) else type(ssr_data)}")
                # Look for any permutation/task data
                if isinstance(ssr_data, dict):
                    for key in ['permutations', 'task', 'taskData', 'puzzleData', 'silhouetteData']:
                        if key in ssr_data:
                            permutations = ssr_data[key]
                            logger.info(f"✅ Found permutations in SSR_DATA.{key}")
                            break
        except Exception as e:
            logger.warning(f"Could not extract SSR data: {e}")
        
        # If no permutations from SSR, try to extract from hidden inputs
        if not permutations:
            try:
                rdata_el = driver.find_element(By.CSS_SELECTOR, "input[name='rdata']")
                rdata_value = rdata_el.get_attribute('value')
                if rdata_value:
                    import base64
                    decoded = base64.b64decode(rdata_value).decode('utf-8')
                    import json
                    permutations = json.loads(decoded)
                    logger.info(f"✅ Extracted permutations from rdata: {type(permutations)}")
            except Exception as e:
                logger.debug(f"Could not extract rdata: {e}")
        
        if not permutations:
            logger.warning("⚠️ No permutation data found, sending empty list")
            permutations = []
        
        # Send to PazlCaptcha V2
        result = capsola.solve_pazl_captcha_v2(image_data, permutations, max_wait=120)
        return result
        
    except Exception as e:
        logger.error(f"❌ PazlCaptcha V2 extraction error: {e}")
        return None


def _apply_silhouette_answer(driver, answer, source_image_data=None) -> bool:
    """Apply the PazlCaptcha answer by clicking at the returned coordinates on the captcha image.
    
    Capsola returns coordinates relative to the SOURCE image dimensions (natural size).
    The displayed <img> element may be CSS-scaled to a different size.
    We must compute scale_x/scale_y to convert source coords → displayed coords.
    
    The answer can be:
    - Coordinates string: "coordinates:x=34.7,y=108.0;x=234.3,y=72.3" 
    - Step number: integer step
    - Comma-separated coords: "x1,y1,x2,y2"
    """
    try:
        logger.info(f"🎯 Applying silhouette answer: {answer}")
        
        # Find the clickable image container
        image_element = None
        image_selectors = [
            "[data-testid='silhouette-container'] img",
            ".AdvancedCaptcha-ImageWrapper img",
            ".AdvancedCaptcha_silhouette img[alt='Image challenge']",
            ".AdvancedCaptcha img[alt='Image challenge']",
        ]
        
        for selector in image_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for el in elements:
                    if el.is_displayed():
                        image_element = el
                        break
            except:
                continue
            if image_element:
                break
        
        if not image_element:
            # Try the container instead
            try:
                image_element = driver.find_element(By.CSS_SELECTOR, ".AdvancedCaptcha-ImageWrapper, [data-testid='silhouette-container']")
            except:
                logger.error("❌ Could not find silhouette image element for clicking")
                return False
        
        # Parse answer
        if isinstance(answer, str):
            # Remove "coordinates:" prefix
            coords_str = answer.replace('coordinates:', '').strip()
            
            # Parse x=...,y=... pairs
            import re
            coord_pairs = re.findall(r'x=([\d.]+),\s*y=([\d.]+)', coords_str)
            
            if coord_pairs:
                logger.info(f"📍 Found {len(coord_pairs)} coordinate pairs to click")
                
                # Displayed element size
                img_size = image_element.size
                displayed_w = img_size['width']
                displayed_h = img_size['height']
                
                # Get natural (source) image dimensions for coordinate scaling
                # Capsola returns coords relative to the source image size
                natural_w, natural_h = displayed_w, displayed_h  # default: no scaling
                
                if source_image_data:
                    try:
                        from PIL import Image
                        from io import BytesIO
                        src_img = Image.open(BytesIO(source_image_data))
                        natural_w, natural_h = src_img.size
                        logger.info(f"📐 Source image: {natural_w}x{natural_h}, displayed: {displayed_w}x{displayed_h}")
                    except Exception as e:
                        logger.warning(f"⚠️ Could not get source image size: {e}")
                else:
                    # Try getting naturalWidth/naturalHeight via JS
                    try:
                        natural_w = driver.execute_script("return arguments[0].naturalWidth", image_element) or displayed_w
                        natural_h = driver.execute_script("return arguments[0].naturalHeight", image_element) or displayed_h
                        logger.info(f"📐 Natural (JS): {natural_w}x{natural_h}, displayed: {displayed_w}x{displayed_h}")
                    except:
                        pass
                
                scale_x = displayed_w / natural_w if natural_w else 1.0
                scale_y = displayed_h / natural_h if natural_h else 1.0
                cx = displayed_w / 2
                cy = displayed_h / 2
                logger.info(f"📐 Scale: x={scale_x:.3f}, y={scale_y:.3f}, center: ({cx:.0f}, {cy:.0f})")
                
                for i, (x_str, y_str) in enumerate(coord_pairs):
                    try:
                        x, y = float(x_str), float(y_str)
                        
                        # Scale source coords to displayed coords, then convert to center-based offset
                        scaled_x = x * scale_x
                        scaled_y = y * scale_y
                        offset_x = int(scaled_x - cx) + random.randint(-1, 1)
                        offset_y = int(scaled_y - cy) + random.randint(-1, 1)
                        
                        ActionChains(driver)\
                            .move_to_element_with_offset(image_element, offset_x, offset_y)\
                            .pause(random.uniform(0.2, 0.5))\
                            .click()\
                            .perform()
                        
                        logger.info(f"✅ Silhouette click {i+1}: raw=({x:.1f}, {y:.1f}), scaled=({scaled_x:.1f}, {scaled_y:.1f}), offset=({offset_x}, {offset_y})")
                        time.sleep(random.uniform(0.3, 0.8))
                    except Exception as e:
                        logger.warning(f"Click error at ({x_str}, {y_str}): {e}")
            else:
                # Try "x1,y1;x2,y2" format or "x1,y1,x2,y2" format
                parts = coords_str.replace(';', ',').split(',')
                if len(parts) >= 2 and len(parts) % 2 == 0:
                    img_size = image_element.size
                    displayed_w2 = img_size['width']
                    displayed_h2 = img_size['height']
                    nat_w2, nat_h2 = displayed_w2, displayed_h2
                    if source_image_data:
                        try:
                            from PIL import Image
                            from io import BytesIO
                            src_img2 = Image.open(BytesIO(source_image_data))
                            nat_w2, nat_h2 = src_img2.size
                        except:
                            pass
                    else:
                        try:
                            nat_w2 = driver.execute_script("return arguments[0].naturalWidth", image_element) or displayed_w2
                            nat_h2 = driver.execute_script("return arguments[0].naturalHeight", image_element) or displayed_h2
                        except:
                            pass
                    s_x2 = displayed_w2 / nat_w2 if nat_w2 else 1.0
                    s_y2 = displayed_h2 / nat_h2 if nat_h2 else 1.0
                    cx = displayed_w2 / 2
                    cy = displayed_h2 / 2
                    for i in range(0, len(parts), 2):
                        try:
                            x = float(parts[i].strip())
                            y = float(parts[i+1].strip())
                            
                            offset_x = int(x * s_x2 - cx)
                            offset_y = int(y * s_y2 - cy)
                            
                            ActionChains(driver)\
                                .move_to_element_with_offset(image_element, offset_x, offset_y)\
                                .pause(random.uniform(0.2, 0.5))\
                                .click()\
                                .perform()
                            
                            logger.info(f"✅ Silhouette click: ({x:.1f}, {y:.1f})")
                            time.sleep(random.uniform(0.3, 0.8))
                        except Exception as e:
                            logger.warning(f"Click error: {e}")
                else:
                    # Maybe it's a step number or other format  
                    logger.info(f"📋 Answer format not recognized as coords, trying as step: {answer}")
                    try:
                        step = int(answer)
                        logger.info(f"📋 Step number: {step} — filling rep field")
                        driver.execute_script(f"""
                            var repInput = document.querySelector('input[name="rep"]');
                            if (repInput) repInput.value = '{step}';
                        """)
                    except (ValueError, TypeError):
                        # Try setting raw value  
                        logger.info(f"📋 Setting raw answer as rep: {answer}")
                        safe_answer = answer.replace("'", "\\'")
                        driver.execute_script(f"""
                            var repInput = document.querySelector('input[name="rep"]');
                            if (repInput) repInput.value = '{safe_answer}';
                        """)
        elif isinstance(answer, (int, float)):
            logger.info(f"📋 Numeric answer: {answer} — filling rep field")
            driver.execute_script(f"""
                var repInput = document.querySelector('input[name="rep"]');
                if (repInput) repInput.value = '{int(answer)}';
            """)
        
        # ШАГ: Submit the form
        time.sleep(random.uniform(0.5, 1.5))
        
        submit_clicked = False
        submit_selectors = [
            "button[data-testid='submit']",
            "[class*='CaptchaButton_view_action']",
            "[class*='AdvancedCaptcha'] button[type='submit']",
            "button[type='submit']",
            "#advanced-captcha-form button[type='submit']",
            "#submit-button",
        ]
        
        for selector in submit_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for el in elements:
                    if el.is_displayed():
                        el.click()
                        submit_clicked = True
                        logger.info(f"✅ Clicked submit button: {selector}")
                        break
            except:
                continue
            if submit_clicked:
                break
        
        if not submit_clicked:
            # Try form submit directly
            try:
                driver.execute_script("""
                    var form = document.getElementById('advanced-captcha-form');
                    if (form) form.submit();
                """)
                submit_clicked = True
                logger.info("✅ Submitted form via JS")
            except:
                pass
        
        # Wait for result — use generous wait since proxy navigation can be slow.
        # The form submit triggers a page load that may take 60+ seconds through proxy.
        time.sleep(random.uniform(5, 8))
        
        # Check if captcha resolved — wrap in try/except for renderer timeout
        try:
            if not detect_captcha_or_block(driver):
                logger.info("🎉 Silhouette/PazlCaptcha solved successfully!")
                return True
        except Exception as e:
            err_str = str(e)
            if 'Timed out' in err_str or 'timeout' in err_str.lower():
                logger.warning(f"⚠️ Renderer timeout after silhouette submit: {err_str[:200]}")
                # Wait more and retry
                time.sleep(10)
                try:
                    if not detect_captcha_or_block(driver):
                        logger.info("🎉 Silhouette solved (detected after timeout recovery)!")
                        return True
                except Exception:
                    pass
            else:
                logger.warning(f"⚠️ Error checking captcha after submit: {err_str[:200]}")
        
        # Check if page redirected
        try:
            current_url = driver.current_url.lower()
            current_url_path = current_url.split('?')[0]
            if 'showcaptcha' not in current_url_path and 'captcha' not in current_url_path:
                logger.info(f"🎉 Redirected away from captcha: {current_url[:100]}")
                return True
        except Exception:
            pass
        
        logger.warning("❌ Silhouette captcha still present after submitting answer")
        return False
        
    except Exception as e:
        logger.error(f"❌ Error applying silhouette answer: {e}")
        import traceback
        traceback.print_exc()
        return False


def _try_capsola_full_screenshot(driver, capsola, screenshot_path: str) -> bool:
    """Try solving captcha using full page screenshot split into click/task parts."""
    try:
        from PIL import Image
        from io import BytesIO
        
        captcha_screenshot = f"screenshots/captcha_full_{int(time.time())}.png"
        driver.save_screenshot(captcha_screenshot)
        
        with open(captcha_screenshot, 'rb') as f:
            full_img = Image.open(f).copy()
        
        w, h = full_img.size
        logger.info(f"📸 Full screenshot: {w}x{h}")
        
        # SmartCaptcha is usually centered — crop to center area
        captcha_top = int(h * 0.15)
        captcha_bottom = int(h * 0.85)
        captcha_left = int(w * 0.25)
        captcha_right = int(w * 0.75)
        
        # Split into click (task description, top part) and task (grid, bottom part)
        split_point = int(captcha_top + (captcha_bottom - captcha_top) * 0.25)
        
        click_crop = full_img.crop((captcha_left, captcha_top, captcha_right, split_point))
        click_buf = BytesIO()
        click_crop.save(click_buf, format='PNG')
        click_image_data = click_buf.getvalue()
        
        task_crop = full_img.crop((captcha_left, split_point, captcha_right, captcha_bottom))
        task_buf = BytesIO()
        task_crop.save(task_buf, format='PNG')
        task_image_data = task_buf.getvalue()
        
        return _send_to_capsola_and_click(driver, capsola, click_image_data, task_image_data, None)
        
    except Exception as e:
        logger.error(f"❌ Full screenshot approach failed: {e}")
        return _try_simple_refresh(driver)


def _send_to_capsola_and_click(driver, capsola, click_image_data: bytes, task_image_data: bytes, grid_element) -> bool:
    """Send captcha images to Capsola and click on the returned coordinates."""
    try:
        logger.info("🔄 Sending SmartCaptcha to Capsola...")
        result = capsola.solve_smart_captcha(click_image_data, task_image_data, max_wait=90)
        
        if not result or result.get('status') != 1:
            logger.error(f"❌ Capsola failed: {result}")
            return _try_simple_refresh(driver)
        
        answer = result.get('response', '')
        logger.info(f"✅ Capsola answer: {answer}")
        
        # Parse coordinates: "coordinates:x=34.7,y=108.0;x=234.3,y=72.3;..."
        if isinstance(answer, str):
            # Remove "coordinates:" prefix if present
            coords_str = answer.replace('coordinates:', '').strip()
            
            # Parse x=...,y=... pairs
            import re
            coord_pairs = re.findall(r'x=([\d.]+),\s*y=([\d.]+)', coords_str)
            
            if coord_pairs:
                # Selenium 4: move_to_element_with_offset uses center-based coords
                grid_cx = 0
                grid_cy = 0
                if grid_element:
                    grid_size = grid_element.size
                    grid_cx = grid_size.get('width', 0) / 2
                    grid_cy = grid_size.get('height', 0) / 2
                    logger.info(f"📐 Grid size: {grid_size.get('width')}x{grid_size.get('height')}")
                
                for x_str, y_str in coord_pairs:
                    try:
                        x, y = float(x_str), float(y_str)
                        
                        if grid_element:
                            # Convert top-left coords to center-based offset
                            ActionChains(driver).move_to_element_with_offset(
                                grid_element, int(x - grid_cx), int(y - grid_cy)
                            ).click().perform()
                        else:
                            # Use JS to click at coordinates
                            driver.execute_script(f"""
                                var el = document.elementFromPoint({int(x)}, {int(y)});
                                if(el) el.click();
                            """)
                        
                        logger.info(f"✅ Clicked ({x:.1f}, {y:.1f})")
                        time.sleep(random.uniform(0.3, 0.8))
                    except Exception as e:
                        logger.warning(f"Click error: {e}")
            else:
                # Maybe simple comma-separated format: "x1,y1;x2,y2"
                clicks = coords_str.replace(';', '\n').strip().split('\n')
                for click_pair in clicks:
                    parts = click_pair.strip().split(',')
                    if len(parts) == 2:
                        try:
                            x, y = float(parts[0].strip()), float(parts[1].strip())
                            if grid_element:
                                ActionChains(driver).move_to_element_with_offset(
                                    grid_element, int(x), int(y)
                                ).click().perform()
                            else:
                                ActionChains(driver).move_by_offset(int(x), int(y)).click().perform()
                            logger.info(f"✅ Clicked ({x:.1f}, {y:.1f})")
                            time.sleep(random.uniform(0.3, 0.8))
                        except Exception as e:
                            logger.warning(f"Click error: {e}")
        
        # Find and click submit button
        time.sleep(1)
        submit_selectors = [
            "[class*='AdvancedCaptcha-SubmitButton']",
            "[class*='Submit']",
            "[class*='CaptchaButton']",
            "button[type='submit']",
        ]
        for selector in submit_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for el in elements:
                    if el.is_displayed():
                        el.click()
                        logger.info(f"✅ Clicked submit: {selector}")
                        break
            except:
                continue
        
        # Wait for result — proxy navigation can be very slow after form submit.
        time.sleep(random.uniform(5, 8))
        
        try:
            if not detect_captcha_or_block(driver):
                logger.info("🎉 SmartCaptcha solved via Capsola!")
                return True
        except Exception as e:
            err_str = str(e)
            if 'Timed out' in err_str or 'timeout' in err_str.lower():
                logger.warning(f"⚠️ Renderer timeout after SmartCaptcha submit: {err_str[:200]}")
                time.sleep(10)
                try:
                    if not detect_captcha_or_block(driver):
                        logger.info("🎉 SmartCaptcha solved (detected after timeout recovery)!")
                        return True
                except Exception:
                    pass
            else:
                logger.warning(f"⚠️ Error checking captcha: {err_str[:200]}")
        
        logger.warning("❌ SmartCaptcha still present after Capsola solution")
        return False
        
    except Exception as e:
        logger.error(f"❌ Capsola click flow error: {e}")
        return False


def _try_simple_refresh(driver) -> bool:
    """Simple retry - refresh and wait."""
    logger.info("🔄 Attempting simple retry by refreshing")
    time.sleep(random.uniform(5, 15))
    driver.refresh()
    time.sleep(random.uniform(5, 10))
    return not detect_captcha_or_block(driver)


def extract_recaptcha_site_key(driver) -> Optional[str]:
    """Extract reCAPTCHA site key from page."""
    try:
        # Look for data-sitekey attribute
        elements = driver.find_elements(By.CSS_SELECTOR, "[data-sitekey]")
        if elements:
            return elements[0].get_attribute("data-sitekey")

        # Look in page source
        page_source = driver.page_source
        matches = re.findall(r'data-sitekey="([^"]+)"', page_source)
        if matches:
            return matches[0]

        # Look for grecaptcha.render calls
        matches = re.findall(r'grecaptcha\.render.*?sitekey.*?["\']([^"\']+)["\']', page_source)
        if matches:
            return matches[0]

        return None

    except Exception as e:
        logger.warning(f"Error extracting reCAPTCHA site key: {e}")
        return None


def _safe_click_maps(driver, element, pause_min=0.3, pause_max=0.8):
    """Safely click an element: scroll into view, try ActionChains, fallback to JS click."""
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});",
            element
        )
        time.sleep(random.uniform(0.3, 0.7))
        driver.execute_script("""
            var elem = arguments[0];
            var rect = elem.getBoundingClientRect();
            if (rect.top < 0 || rect.bottom > window.innerHeight) {
                elem.scrollIntoView({block: 'center'});
            }
        """, element)
        time.sleep(random.uniform(0.2, 0.4))
        ActionChains(driver).move_to_element(element).pause(
            random.uniform(pause_min, pause_max)
        ).click().perform()
    except Exception as e:
        try:
            driver.execute_script("arguments[0].click();", element)
        except Exception as js_err:
            logger.warning(f"_safe_click_maps: both ActionChains ({e}) and JS ({js_err}) failed")
            raise


def search_company_on_maps(driver, search_query: str, target_url: str, captcha_solver=None, task_id: int = None, profile_id: int = None) -> dict:
    """
    Search for a company on Yandex Maps and navigate to its profile card.
    
    Flow:
    1. Open https://yandex.ru/maps/
    2. Type the company name into the search bar
    3. Find and click the company card in search results
    4. Verify we landed on the correct company page
    
    Args:
        driver: Selenium WebDriver
        search_query: Company name to search for (e.g. "Benesque")
        target_url: Expected target URL to match against (contains org ID)
        captcha_solver: Optional captcha solver instance
        task_id: Task ID for logging
        profile_id: Profile ID for logging
        
    Returns:
        dict with keys: success (bool), found_url (str), method (str), error (str)
    """
    result = {'success': False, 'found_url': None, 'method': 'search', 'error': None}
    
    # Extract org ID from target URL for matching (e.g. "193289471730" from the URL)
    org_id = None
    try:
        # URL like https://yandex.com/maps/org/benesque/193289471730/
        url_parts = target_url.rstrip('/').split('/')
        for part in url_parts:
            if part.isdigit() and len(part) > 5:
                org_id = part
                break
    except Exception:
        pass
    
    # Also extract org name from URL for fuzzy matching
    org_name_from_url = None
    try:
        url_parts = target_url.rstrip('/').split('/')
        org_idx = None
        for i, part in enumerate(url_parts):
            if part == 'org':
                org_idx = i
                break
        if org_idx is not None and org_idx + 1 < len(url_parts):
            org_name_from_url = url_parts[org_idx + 1].replace('-', ' ').lower()
    except Exception:
        pass

    logger.info(f"🔍 Maps search: query='{search_query}', org_id={org_id}, org_name_from_url={org_name_from_url}")
    if task_id and profile_id:
        _update_task_log(profile_id, target_url, f"🔍 Ищем компанию: '{search_query}'", task_id=task_id)
    
    # Step 1: Navigate to Yandex Maps
    maps_url = "https://yandex.ru/maps/"
    logger.info(f"🗺️ Step 1: Opening Yandex Maps: {maps_url}")
    
    try:
        driver.get(maps_url)
    except Exception as e:
        error_msg = str(e)
        logger.error(f"❌ Failed to open Yandex Maps: {error_msg}")
        result['error'] = f"Failed to open maps: {error_msg[:200]}"
        return result
    
    time.sleep(random.uniform(3, 5))
    
    actual_url = driver.current_url
    logger.info(f"📍 Maps loaded: {actual_url}")
    
    # Check for captcha on maps page
    if detect_captcha_or_block(driver):
        logger.warning("⚠️ Captcha on Yandex Maps, attempting to solve")
        if task_id and profile_id:
            _update_task_log(profile_id, target_url, "⚠️ Капча на Яндекс Картах, решаем...", task_id=task_id)
        if captcha_solver and handle_yandex_protection(driver, captcha_solver):
            logger.info("✅ Captcha solved on maps")
            if task_id and profile_id:
                _update_task_log(profile_id, target_url, "✅ Капча решена!", task_id=task_id)
            time.sleep(random.uniform(2, 4))
            # Re-navigate to maps after captcha
            try:
                driver.get(maps_url)
                time.sleep(random.uniform(3, 5))
            except Exception:
                pass
        else:
            logger.error("❌ Could not solve captcha on maps")
            result['error'] = "Captcha not solved on maps"
            return result
    
    # Step 2: Find and type into the search input
    logger.info(f"⌨️ Step 2: Typing search query '{search_query}'")
    if task_id and profile_id:
        _update_task_log(profile_id, target_url, f"⌨️ Вводим запрос: '{search_query}'", task_id=task_id)
    
    search_input = None
    maps_input_selectors = [
        # Yandex Maps search input selectors
        "input.input__control",
        "input.suggest-search-input__input",
        "input[class*='input__control']",
        "input[class*='search-input']",
        "input[placeholder*='Поиск']",
        "input[placeholder*='поиск']",
        "input[placeholder*='Адрес']",
        "input[placeholder*='Search']",
        "input[aria-label*='Поиск']",
        "input[aria-label*='Search']",
        "header input[type='text']",
        ".search-form-view input",
        ".sidebar-header input",
        ".search-snippet-view input",
        # Generic
        "input[name='text']",
        "input[role='searchbox']",
        "input[role='combobox']",
        "form input[type='text']",
        "form input[type='search']",
    ]
    
    for selector in maps_input_selectors:
        try:
            elems = driver.find_elements(By.CSS_SELECTOR, selector)
            for elem in elems:
                try:
                    if elem.is_displayed() and elem.is_enabled():
                        logger.info(f"   ✅ Found maps search input: selector='{selector}'")
                        search_input = elem
                        break
                except Exception:
                    continue
            if search_input:
                break
        except Exception:
            continue
    
    # JS fallback for maps search input
    if not search_input:
        logger.info("   CSS selectors failed, trying JS for maps search input...")
        try:
            js_result = driver.execute_script("""
                var inputs = document.querySelectorAll('input');
                for (var i = 0; i < inputs.length; i++) {
                    var el = inputs[i];
                    var rect = el.getBoundingClientRect();
                    if (rect.width > 100 && rect.height > 10 && el.offsetParent !== null &&
                        el.type !== 'hidden' && el.type !== 'checkbox' && el.type !== 'radio') {
                        return { id: el.id, name: el.name, class: el.className.substring(0, 80) };
                    }
                }
                return null;
            """)
            if js_result:
                logger.info(f"   JS found input: {js_result}")
                if js_result.get('id'):
                    search_input = driver.find_element(By.ID, js_result['id'])
                elif js_result.get('name'):
                    search_input = driver.find_element(By.NAME, js_result['name'])
        except Exception as js_err:
            logger.warning(f"   JS input search failed: {js_err}")
    
    if not search_input:
        logger.error("❌ Could not find search input on Yandex Maps")
        result['error'] = "Search input not found on maps"
        return result
    
    # Click and type
    try:
        _safe_click_maps(driver, search_input, 0.3, 0.7)
        time.sleep(random.uniform(0.5, 1.0))
        
        # Clear existing text
        try:
            search_input.clear()
        except Exception:
            driver.execute_script("arguments[0].value = '';", search_input)
        time.sleep(random.uniform(0.2, 0.5))
        
        # Type character by character with human-like delays
        logger.info(f"   Typing '{search_query}' character by character...")
        for i, char in enumerate(search_query):
            search_input.send_keys(char)
            if char == ' ':
                time.sleep(random.uniform(0.15, 0.4))
            elif i < 2:
                time.sleep(random.uniform(0.1, 0.3))
            else:
                time.sleep(random.uniform(0.04, 0.18))
        
        logger.info(f"   Query typed. Waiting for suggestions/results...")
        time.sleep(random.uniform(1.5, 3.0))
        
    except Exception as type_err:
        logger.warning(f"   Typing failed: {type_err}, trying JS fallback")
        try:
            driver.execute_script("""
                var input = arguments[0];
                var text = arguments[1];
                input.focus();
                var nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                nativeInputValueSetter.call(input, text);
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
            """, search_input, search_query)
            time.sleep(random.uniform(1.5, 3.0))
        except Exception as js_type_err:
            logger.error(f"❌ Both typing methods failed: {js_type_err}")
            result['error'] = f"Failed to type search query: {type_err}"
            return result
    
    # Step 3: Submit search (press Enter or click search button)
    logger.info("🔎 Step 3: Submitting search...")
    
    search_submitted = False
    search_button_selectors = [
        "button.small-search-form-view__button",
        "button[type='submit']",
        "button[class*='search']",
        "button[aria-label*='Найти']",
        "button[aria-label*='Search']",
        ".search-form-view__button",
        "form button",
    ]
    
    for btn_sel in search_button_selectors:
        try:
            buttons = driver.find_elements(By.CSS_SELECTOR, btn_sel)
            for btn in buttons:
                if btn.is_displayed() and btn.is_enabled():
                    logger.info(f"   Found search button: {btn_sel}")
                    _safe_click_maps(driver, btn, 0.2, 0.5)
                    search_submitted = True
                    break
        except Exception:
            continue
        if search_submitted:
            break
    
    if not search_submitted:
        logger.info("   No search button found, pressing Enter...")
        try:
            search_input.send_keys(Keys.RETURN)
            search_submitted = True
        except Exception:
            try:
                driver.execute_script("""
                    var form = arguments[0].closest('form');
                    if (form) form.submit();
                    else arguments[0].dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', keyCode: 13, bubbles: true}));
                """, search_input)
                search_submitted = True
            except Exception as sub_err:
                logger.error(f"❌ Could not submit search: {sub_err}")
                result['error'] = f"Failed to submit search: {sub_err}"
                return result
    
    # Wait for search results to load
    logger.info("⏳ Waiting for maps search results...")
    time.sleep(random.uniform(4, 7))
    
    # Check for captcha after search
    if detect_captcha_or_block(driver):
        logger.warning("⚠️ Captcha after maps search")
        if task_id and profile_id:
            _update_task_log(profile_id, target_url, "⚠️ Капча после поиска, решаем...", task_id=task_id)
        if captcha_solver and handle_yandex_protection(driver, captcha_solver):
            logger.info("✅ Captcha solved after search")
            time.sleep(random.uniform(3, 5))
        else:
            result['error'] = "Captcha not solved after search"
            return result
    
    # Step 4: Find the target company in search results
    logger.info(f"🏢 Step 4: Looking for company in search results...")
    if task_id and profile_id:
        _update_task_log(profile_id, target_url, "🏢 Ищем компанию в результатах поиска...", task_id=task_id)
    
    company_found = False
    found_element = None
    
    # Strategy 1: Look for a link containing the org ID
    if org_id:
        try:
            links = driver.find_elements(By.CSS_SELECTOR, f"a[href*='{org_id}']")
            for link in links:
                if link.is_displayed():
                    logger.info(f"   ✅ Found link with org_id {org_id}: {link.get_attribute('href')[:120]}")
                    found_element = link
                    company_found = True
                    break
        except Exception as e:
            logger.warning(f"   org_id search failed: {e}")
    
    # Strategy 2: Look for search result items containing the company name
    if not company_found:
        search_result_selectors = [
            ".search-snippet-view",
            ".search-list-view .search-snippet-view__body",
            "[class*='search-snippet']",
            ".search-business-snippet-view",
            "[class*='SearchSnippet']",
            ".search-list-view li",
            # Card/list items
            "[class*='card-title']",
            "[class*='business-card']",
        ]
        
        search_query_lower = search_query.lower()
        
        for sel in search_result_selectors:
            try:
                items = driver.find_elements(By.CSS_SELECTOR, sel)
                for item in items:
                    try:
                        item_text = item.text.lower()
                        if not item.is_displayed():
                            continue
                        # Check if company name appears in the result
                        if search_query_lower in item_text:
                            logger.info(f"   ✅ Found company by text match in '{sel}': {item_text[:80]}")
                            # Try to find a clickable link inside
                            links_inside = item.find_elements(By.TAG_NAME, 'a')
                            if links_inside:
                                found_element = links_inside[0]
                            else:
                                found_element = item
                            company_found = True
                            break
                    except Exception:
                        continue
                if company_found:
                    break
            except Exception:
                continue
    
    # Strategy 3: Look for /org/ links in ALL search results (prefer org pages over geo)
    if not company_found:
        logger.info("   Company not found by name/ID, looking for /org/ links in results...")
        try:
            org_links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/org/']")
            for link in org_links:
                try:
                    if link.is_displayed():
                        href = link.get_attribute('href') or ''
                        link_text = link.text.strip()
                        logger.info(f"   Found /org/ link: {link_text[:60]} → {href[:120]}")
                        found_element = link
                        company_found = True
                        break
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"   /org/ link search failed: {e}")
    
    # Strategy 4: JS search for org links in sidebar/results
    if not company_found:
        logger.info("   Trying JS to find /org/ links...")
        try:
            js_click = driver.execute_script("""
                // Priority 1: any link to an /org/ page
                var orgLinks = document.querySelectorAll('a[href*="/org/"]');
                for (var i = 0; i < orgLinks.length; i++) {
                    var rect = orgLinks[i].getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        return { href: orgLinks[i].href, text: orgLinks[i].textContent.substring(0, 80), type: 'org' };
                    }
                }
                // Priority 2: search snippets (business results)
                var snippets = document.querySelectorAll('.search-snippet-view, [class*="search-snippet"], [class*="SearchSnippet"], .search-business-snippet-view');
                if (snippets.length > 0) {
                    var first = snippets[0];
                    var link = first.querySelector('a');
                    if (link) {
                        return { href: link.href, text: link.textContent.substring(0, 80), type: 'snippet' };
                    }
                    return { href: null, text: first.textContent.substring(0, 80), type: 'snippet_no_link' };
                }
                // Priority 3: any card in sidebar
                var cards = document.querySelectorAll('.sidebar-view a[href*="/org/"]');
                if (cards.length > 0) {
                    return { href: cards[0].href, text: cards[0].textContent.substring(0, 80), type: 'sidebar' };
                }
                return null;
            """)
            if js_click:
                logger.info(f"   JS found result ({js_click.get('type', '?')}): {js_click}")
                if js_click.get('href'):
                    links = driver.find_elements(By.CSS_SELECTOR, f"a[href='{js_click['href']}']")
                    if links:
                        found_element = links[0]
                        company_found = True
        except Exception as js_err:
            logger.warning(f"   JS result search failed: {js_err}")
    
    # Strategy 5: Direct navigation fallback — if search doesn't show the org, go directly
    if not company_found and org_id:
        logger.info(f"   Search didn't find org, falling back to direct URL: {target_url}")
        if task_id and profile_id:
            _update_task_log(profile_id, target_url, "🔄 Компания не найдена в поиске, переходим напрямую...", task_id=task_id)
        try:
            driver.get(target_url)
            time.sleep(random.uniform(3, 6))
            final_url = driver.current_url
            if '/org/' in final_url or org_id in final_url:
                logger.info(f"   ✅ Direct navigation succeeded: {final_url[:120]}")
                result['success'] = True
                result['found_url'] = final_url
                result['method'] = 'direct_fallback'
                if task_id and profile_id:
                    _update_task_log(profile_id, target_url, f"✅ Карточка компании открыта (прямой переход): {final_url[:100]}", task_id=task_id)
                return result
            else:
                logger.warning(f"   Direct navigation landed on: {final_url[:120]}")
        except Exception as nav_err:
            logger.warning(f"   Direct navigation failed: {nav_err}")
    
    # Strategy 6: Click first search result only if it's a business (not geo)
    if not company_found:
        logger.info("   Last resort: trying first search result...")
        first_result_selectors = [
            ".search-business-snippet-view:first-child",
            ".search-snippet-view:first-child",
            ".search-list-view li:first-child",
            "[class*='search-snippet']:first-child",
        ]
        
        for sel in first_result_selectors:
            try:
                items = driver.find_elements(By.CSS_SELECTOR, sel)
                for item in items:
                    if item.is_displayed():
                        # Check if this result has an /org/ link (business) vs /geo/ (geographic)
                        org_link_inside = item.find_elements(By.CSS_SELECTOR, "a[href*='/org/']")
                        if org_link_inside:
                            logger.info(f"   Using first business result: {item.text[:60]}")
                            found_element = org_link_inside[0]
                            company_found = True
                            break
                        else:
                            # Skip geo results — they won't have our org
                            logger.info(f"   Skipping non-org result: {item.text[:60]}")
                if company_found:
                    break
            except Exception:
                continue
    
    if not company_found or not found_element:
        logger.warning(f"❌ Company '{search_query}' not found in maps search results")
        if task_id and profile_id:
            _update_task_log(profile_id, target_url, f"❌ Компания '{search_query}' не найдена в результатах", task_id=task_id)
        # Take screenshot for debugging
        try:
            ts = int(time.time())
            driver.save_screenshot(f"/app/screenshots/maps_search_notfound_{ts}.png")
            logger.info(f"   Screenshot saved: maps_search_notfound_{ts}.png")
        except Exception:
            pass
        result['error'] = f"Company '{search_query}' not found in maps search results"
        return result
    
    # Step 5: Click on the company card
    logger.info("👆 Step 5: Clicking on company card...")
    if task_id and profile_id:
        _update_task_log(profile_id, target_url, "👆 Кликаем на карточку компании...", task_id=task_id)
    
    try:
        # Small pause before clicking (human behavior)
        time.sleep(random.uniform(0.5, 1.5))
        _safe_click_maps(driver, found_element, 0.3, 0.8)
        time.sleep(random.uniform(3, 6))
    except Exception as click_err:
        logger.warning(f"   Click failed: {click_err}, trying JS click")
        try:
            driver.execute_script("arguments[0].click();", found_element)
            time.sleep(random.uniform(3, 6))
        except Exception as js_click_err:
            logger.error(f"❌ Both click methods failed: {js_click_err}")
            result['error'] = f"Failed to click company card: {click_err}"
            return result
    
    # Verify we're on a company page
    final_url = driver.current_url
    logger.info(f"📍 After click URL: {final_url}")
    
    # Check if we landed on an org page
    is_on_org_page = '/org/' in final_url
    is_on_geo_page = '/geo/' in final_url
    
    # If org_id specified, check it matches
    if org_id and org_id in final_url:
        logger.info(f"✅ Successfully navigated to correct company page (org_id match)")
        result['success'] = True
        result['found_url'] = final_url
    elif is_on_org_page:
        logger.info(f"✅ On an org page (may not match exact target): {final_url[:120]}")
        result['success'] = True
        result['found_url'] = final_url
    elif is_on_geo_page and org_id:
        # We're on a geographic page, not an organization — this is wrong
        logger.warning(f"⚠️ Landed on geo page instead of org: {final_url[:120]}")
        logger.info(f"   Attempting direct navigation to target org...")
        try:
            driver.get(target_url)
            time.sleep(random.uniform(3, 6))
            redirect_url = driver.current_url
            if '/org/' in redirect_url or org_id in redirect_url:
                logger.info(f"✅ Direct fallback succeeded: {redirect_url[:120]}")
                result['success'] = True
                result['found_url'] = redirect_url
                result['method'] = 'direct_fallback_after_geo'
            else:
                logger.warning(f"❌ Direct fallback also failed: {redirect_url[:120]}")
                result['success'] = False
                result['found_url'] = redirect_url
                result['error'] = f"Search led to geo page, direct navigation failed"
        except Exception as nav_err:
            logger.error(f"❌ Direct navigation after geo failed: {nav_err}")
            result['success'] = False
            result['error'] = f"Geo page reached, direct fallback failed: {nav_err}"
    else:
        # We might be on the map with the card open in sidebar — this is also a valid state
        # Check if sidebar has company info
        try:
            sidebar_title = driver.execute_script("""
                var title = document.querySelector('.card-title-view__title, [class*="card-title"], .business-card-title-view__title');
                return title ? title.textContent.trim() : null;
            """)
            if sidebar_title:
                logger.info(f"✅ Company card opened in sidebar: '{sidebar_title}'")
                result['success'] = True
                result['found_url'] = final_url
            else:
                logger.warning(f"⚠️ Not on org page and no sidebar card: {final_url[:120]}")
                # Last resort: try direct navigation
                if org_id:
                    logger.info(f"   Last resort: direct navigation to {target_url}")
                    try:
                        driver.get(target_url)
                        time.sleep(random.uniform(3, 6))
                        redirect_url = driver.current_url
                        if '/org/' in redirect_url or org_id in redirect_url:
                            logger.info(f"✅ Direct fallback succeeded: {redirect_url[:120]}")
                            result['success'] = True
                            result['found_url'] = redirect_url
                            result['method'] = 'direct_fallback_last_resort'
                        else:
                            result['success'] = False
                            result['found_url'] = redirect_url
                            result['error'] = f"Could not reach org page"
                    except Exception:
                        result['success'] = False
                        result['error'] = f"Could not reach org page"
                else:
                    result['success'] = True  # No org_id to verify — accept the result
                    result['found_url'] = final_url
        except Exception:
            result['success'] = True
            result['found_url'] = final_url
    
    if task_id and profile_id:
        _update_task_log(profile_id, target_url, f"✅ Карточка компании открыта: {final_url[:100]}", task_id=task_id)
    
    return result


def perform_yandex_visit_actions(browser_manager: BrowserManager, browser_id: str, params: Dict) -> Dict:
    """Perform realistic actions on Yandex Maps profile page."""
    driver = browser_manager.active_browsers[browser_id]
    results = {
        'actions_performed': [],
        'elements_interacted': 0,
        'scroll_actions': 0,
        'clicks_performed': 0
    }

    try:
        # Build list of possible actions and RANDOMIZE order
        possible_actions = []
        actions = params['actions']

        if ('scroll' in actions) and random.random() < params['scroll_probability']:
            possible_actions.append('page_scroll')

        if ('view_photos' in actions or 'photos' in actions) and random.random() < params['photo_click_probability']:
            possible_actions.append('view_photos')

        if ('read_reviews' in actions or 'reviews' in actions) and random.random() < params['review_read_probability']:
            possible_actions.append('read_reviews')

        if ('click_contacts' in actions or 'contacts' in actions) and random.random() < params['contact_click_probability']:
            possible_actions.append('click_contacts')

        if ('view_map' in actions or 'map' in actions) and random.random() < params['map_interaction_probability']:
            possible_actions.append('view_map')
        # Always start with a scroll to look natural
        if 'page_scroll' in possible_actions:
            possible_actions.remove('page_scroll')
            scroll_count = perform_realistic_scrolling(driver)
            results['scroll_actions'] += scroll_count
            results['actions_performed'].append('page_scroll')
            time.sleep(random.uniform(0.5, 2.0))

        # Shuffle remaining actions for random order
        random.shuffle(possible_actions)

        for action_name in possible_actions:
            # Random micro-pause between actions (like a human thinking)
            time.sleep(random.uniform(1.0, 3.0))

            if action_name == 'view_photos':
                if click_photos_section(driver):
                    results['clicks_performed'] += 1
                    results['elements_interacted'] += 1
                    results['actions_performed'].append('viewed_photos')
                    time.sleep(random.uniform(3, 8))

            elif action_name == 'read_reviews':
                reviews_read = read_reviews_section(driver)
                if reviews_read > 0:
                    results['elements_interacted'] += reviews_read
                    results['actions_performed'].append(f'read_{reviews_read}_reviews')

            elif action_name == 'click_contacts':
                if click_contact_info(driver):
                    results['clicks_performed'] += 1
                    results['elements_interacted'] += 1
                    results['actions_performed'].append('viewed_contacts')

            elif action_name == 'view_map':
                if interact_with_map(driver):
                    results['actions_performed'].append('map_interaction')
                    results['elements_interacted'] += 1

        # Random additional scrolling
        if random.random() < 0.5:
            additional_scrolls = perform_realistic_scrolling(driver, max_scrolls=3)
            results['scroll_actions'] += additional_scrolls

        logger.info(f"Performed {len(results['actions_performed'])} actions on Yandex Maps profile")

    except Exception as e:
        logger.error(f"Error performing Yandex visit actions: {e}", exc_info=True)

    return results


def perform_realistic_scrolling(driver, max_scrolls: int = 5) -> int:
    """Perform realistic scrolling behavior."""
    try:
        total_height = driver.execute_script("return document.body.scrollHeight")
        viewport_height = driver.execute_script("return window.innerHeight")

        if total_height <= viewport_height:
            return 0  # No need to scroll

        scroll_count = 0
        current_position = 0

        for _ in range(random.randint(2, max_scrolls)):
            # Random scroll distance
            scroll_distance = random.randint(200, 600)
            current_position += scroll_distance

            # Don't scroll past the end
            if current_position > total_height - viewport_height:
                current_position = total_height - viewport_height

            # Smooth scroll in small increments (like mouse wheel)
            steps = random.randint(4, 10)
            prev_pos = driver.execute_script("return window.pageYOffset") or 0
            step_size = (current_position - prev_pos) / steps
            for s in range(steps):
                intermediate = int(prev_pos + step_size * (s + 1))
                driver.execute_script(f"window.scrollTo({{top: {intermediate}, behavior: 'smooth'}});")
                time.sleep(random.uniform(0.02, 0.06))

            scroll_count += 1

            # Pause to "read" content
            pause_time = random.uniform(1.5, 4)
            time.sleep(pause_time)

            # Sometimes scroll back up a bit
            if random.random() < 0.3:
                back_scroll = random.randint(100, 300)
                current_position = max(0, current_position - back_scroll)
                driver.execute_script(f"window.scrollTo({{top: {current_position}, behavior: 'smooth'}});")
                time.sleep(random.uniform(0.5, 1.5))

        return scroll_count

    except Exception as e:
        logger.warning(f"Error during scrolling: {e}")
        return 0


def click_photos_section(driver) -> bool:
    """Click on photos section if available."""
    try:
        # Common selectors for Yandex Maps photo sections
        photo_selectors = [
            ".photos-view__item", ".business-photos-view__item",
            ".gallery-item", "[data-bem*='photo']", ".photo-item",
            "img[src*='avatars.mds.yandex']", ".business-gallery-item"
        ]

        for selector in photo_selectors:
            try:
                photos = driver.find_elements(By.CSS_SELECTOR, selector)
                if photos and len(photos) > 0:
                    # Click on first available photo using real mouse events
                    photo = photos[0]
                    if photo.is_displayed() and photo.is_enabled():
                        ActionChains(driver).move_to_element(photo).pause(
                            random.uniform(0.1, 0.3)
                        ).click().perform()
                        logger.info("Clicked on photo")

                        # Wait for photo viewer to load
                        time.sleep(random.uniform(1, 3))

                        # Close photo viewer
                        close_selectors = [
                            ".modal-close", ".popup-close", ".close-button",
                            "[data-bem*='close']", ".gallery-close"
                        ]

                        for close_selector in close_selectors:
                            try:
                                close_btn = driver.find_element(By.CSS_SELECTOR, close_selector)
                                if close_btn.is_displayed():
                                    close_btn.click()
                                    break
                            except:
                                continue

                        # Fallback: press Escape key
                        try:
                            driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.ESCAPE)
                        except:
                            pass

                        time.sleep(random.uniform(1, 2))
                        return True

            except Exception as e:
                logger.debug(f"Error clicking photo with selector {selector}: {e}")
                continue

        return False

    except Exception as e:
        logger.warning(f"Error clicking photos section: {e}")
        return False


def read_reviews_section(driver) -> int:
    """Read reviews section by scrolling and pausing."""
    try:
        reviews_read = 0

        # Look for reviews section
        review_selectors = [
            ".business-reviews-card-view__review", ".review-item",
            "[data-bem*='review']", ".reviews-list .review",
            ".business-review-view"
        ]

        reviews = []
        for selector in review_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                if elements:
                    reviews = elements
                    break
            except:
                continue

        if not reviews:
            return 0

        # Read first few reviews
        for i, review in enumerate(reviews[:3]):
            try:
                if review.is_displayed():
                    # Smooth scroll to review
                    driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", review)
                    time.sleep(random.uniform(0.8, 2.0))

                    # "Read" review (pause time based on estimated length)
                    try:
                        review_text = review.text
                        read_time = min(max(len(review_text) / 200, 1), 6)  # 1-6 seconds
                        time.sleep(random.uniform(read_time * 0.7, read_time * 1.3))
                        reviews_read += 1
                    except:
                        time.sleep(random.uniform(1, 3))
                        reviews_read += 1

            except Exception as e:
                logger.debug(f"Error reading review {i}: {e}")
                continue

        logger.info(f"Read {reviews_read} reviews")
        return reviews_read

    except Exception as e:
        logger.warning(f"Error reading reviews: {e}")
        return 0


def click_contact_info(driver) -> bool:
    """Click on contact information elements."""
    try:
        contact_selectors = [
            ".business-contacts-view__phone", ".phone-link",
            ".business-card-view__address", ".address-link",
            "[data-bem*='phone']", "[data-bem*='address']",
            ".contact-info", ".business-phone"
        ]

        for selector in contact_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                if elements:
                    element = elements[0]
                    if element.is_displayed() and element.is_enabled():
                        # Smooth scroll to element
                        driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", element)
                        time.sleep(random.uniform(0.8, 1.5))

                        # Click with real mouse events
                        ActionChains(driver).move_to_element(element).pause(
                            random.uniform(0.1, 0.3)
                        ).click().perform()
                        logger.info("Clicked contact information")

                        # Wait a bit
                        time.sleep(random.uniform(2, 4))
                        return True

            except Exception as e:
                logger.debug(f"Error clicking contact with selector {selector}: {e}")
                continue

        return False

    except Exception as e:
        logger.warning(f"Error clicking contact info: {e}")
        return False


def interact_with_map(driver) -> bool:
    """Interact with the map element on Yandex Maps page."""
    try:
        # Updated selectors for current Yandex Maps layout (2024-2026)
        map_selectors = [
            # Modern Yandex Maps selectors
            "[class*='map-container']",
            "[class*='card-map']",
            "[class*='orgpage-map']",
            ".orgpage-map-provider__map",
            "[class*='MapComponent']",
            # ymaps3 (new API)
            "[class*='ymaps3']",
            "ymaps[class*='map']",
            # ymaps 2.1 legacy
            ".ymaps-2-1-79-map",
            "[class*='ymaps'][class*='map']",
            ".ymaps-map",
            ".ymaps-glass",
            # Generic fallbacks
            "[data-bem*='map']",
            ".map-container",
            ".business-map-view",
            "canvas[class*='map']",
            # iframe with map
        ]

        map_element = None
        for selector in map_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for el in elements:
                    if el.is_displayed() and el.size['width'] > 80 and el.size['height'] > 80:
                        map_element = el
                        break
                if map_element:
                    break
            except Exception as e:
                logger.debug(f"Selector {selector} failed: {e}")
                continue

        # Fallback: try to find map via JavaScript (look for large canvas/div with map-like attributes)
        if not map_element:
            try:
                map_element = driver.execute_script("""
                    // Try ymaps global object
                    var candidates = document.querySelectorAll(
                        '[class*="ymaps"], [class*="map-container"], [class*="orgpage-map"], canvas'
                    );
                    for (var i = 0; i < candidates.length; i++) {
                        var el = candidates[i];
                        var rect = el.getBoundingClientRect();
                        if (rect.width > 100 && rect.height > 100 && rect.top < window.innerHeight * 2) {
                            return el;
                        }
                    }
                    return null;
                """)
            except Exception as e:
                logger.debug(f"JS map search failed: {e}")

        if not map_element:
            logger.info("Map element not found on page")
            return False

        # Smooth scroll to map
        driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", map_element)
        time.sleep(random.uniform(1, 2))

        # Get map dimensions
        size = map_element.size
        if size['width'] < 50 or size['height'] < 50:
            logger.info(f"Map element too small: {size}")
            return False

        # Perform random interactions on map
        num_clicks = random.randint(1, 3)
        for i in range(num_clicks):
            # Keep offsets within the element, offset from center
            x_offset = random.randint(-size['width'] // 3, size['width'] // 3)
            y_offset = random.randint(-size['height'] // 3, size['height'] // 3)

            try:
                ActionChains(driver).move_to_element_with_offset(
                    map_element, x_offset, y_offset
                ).pause(random.uniform(0.2, 0.5)).click().perform()
            except Exception as click_err:
                logger.debug(f"Map click {i+1} failed: {click_err}")
                # Fallback: try JS click at position
                try:
                    driver.execute_script("""
                        var rect = arguments[0].getBoundingClientRect();
                        var cx = rect.left + rect.width/2 + arguments[1];
                        var cy = rect.top + rect.height/2 + arguments[2];
                        var evt = new MouseEvent('click', {
                            bubbles: true, cancelable: true, view: window,
                            clientX: cx, clientY: cy
                        });
                        document.elementFromPoint(cx, cy).dispatchEvent(evt);
                    """, map_element, x_offset, y_offset)
                except Exception:
                    pass

            time.sleep(random.uniform(0.8, 2.0))

        # Optional: try drag (pan) on map
        if random.random() < 0.4:
            try:
                start_x = random.randint(-size['width'] // 4, size['width'] // 4)
                start_y = random.randint(-size['height'] // 4, size['height'] // 4)
                end_x = start_x + random.randint(-100, 100)
                end_y = start_y + random.randint(-100, 100)
                ActionChains(driver).move_to_element_with_offset(map_element, start_x, start_y) \
                    .click_and_hold() \
                    .pause(random.uniform(0.1, 0.3)) \
                    .move_by_offset(end_x - start_x, end_y - start_y) \
                    .pause(random.uniform(0.1, 0.2)) \
                    .release().perform()
                time.sleep(random.uniform(0.5, 1.5))
            except Exception as drag_err:
                logger.debug(f"Map drag failed: {drag_err}")

        logger.info(f"Interacted with map ({num_clicks} clicks)")
        return True

    except Exception as e:
        logger.warning(f"Error interacting with map: {e}")
        return False


def perform_passive_browsing(browser_manager: BrowserManager, browser_id: str, duration: float):
    """Perform passive browsing activities for remaining time."""
    try:
        driver = browser_manager.active_browsers[browser_id]
        end_time = time.time() + duration

        while time.time() < end_time:
            action = random.choice([
                'scroll_small', 'mouse_move', 'pause_long', 'scroll_up'
            ])

            if action == 'scroll_small':
                scroll_distance = random.randint(50, 200)
                direction = random.choice([1, -1])  # Up or down
                driver.execute_script(f"window.scrollBy({{top: {scroll_distance * direction}, behavior: 'smooth'}});")

            elif action == 'mouse_move':
                # Move mouse to ABSOLUTE position via body element
                try:
                    body = driver.find_element(By.TAG_NAME, 'body')
                    viewport_width = driver.execute_script("return window.innerWidth")
                    viewport_height = driver.execute_script("return window.innerHeight")

                    x = random.randint(50, max(100, viewport_width - 50))
                    y = random.randint(50, max(100, viewport_height - 50))

                    # Move to body first (resets position), then offset
                    ActionChains(driver).move_to_element_with_offset(
                        body, x, y
                    ).perform()
                except Exception:
                    pass

            elif action == 'pause_long':
                # Longer pause as if reading
                time.sleep(random.uniform(5, 15))
                continue

            elif action == 'scroll_up':
                # Smooth scroll back to top sometimes
                driver.execute_script("window.scrollTo({top: 0, behavior: 'smooth'});")

            # Random pause between actions
            time.sleep(random.uniform(2, 6))

    except Exception as e:
        logger.warning(f"Error during passive browsing: {e}")


@shared_task(base=BaseTask, bind=True)
def batch_visit_yandex_profiles_task(self, profile_urls: List[Dict], batch_parameters: Dict = None):
    """
    Visit multiple Yandex Maps profiles in a batch.

    Args:
        profile_urls: List of dicts with profile_id and target_url
        batch_parameters: Parameters for the batch operation
    """
    try:
        logger.info(f"Starting batch visit for {len(profile_urls)} Yandex Maps profiles")

        # Default batch parameters
        default_batch_params = {
            'delay_between_visits': settings.batch_delay_seconds,  # Configurable, default 30s
            'randomize_order': True,
            'continue_on_error': True
        }

        if batch_parameters:
            default_batch_params.update(batch_parameters)

        results = []
        urls_to_visit = profile_urls.copy()

        # Randomize order if requested
        if default_batch_params['randomize_order']:
            random.shuffle(urls_to_visit)

        for i, url_data in enumerate(urls_to_visit):
            try:
                profile_id = url_data['profile_id']
                target_url = url_data['target_url']
                visit_params = url_data.get('parameters', {})

                logger.info(f"Batch visit {i+1}/{len(urls_to_visit)}: Profile {profile_id}")

                # Fire-and-forget: dispatch task without blocking
                visit_yandex_maps_profile_task.delay(profile_id, target_url, visit_params)

                results.append({
                    'profile_id': profile_id,
                    'target_url': target_url,
                    'status': 'dispatched',
                })

                # Short delay between dispatches (not between completions)
                if i < len(urls_to_visit) - 1:
                    delay = random.randint(
                        max(5, int(default_batch_params['delay_between_visits'] * 0.8)),
                        int(default_batch_params['delay_between_visits'] * 1.2)
                    )
                    logger.info(f"Waiting {delay} seconds before next dispatch")
                    time.sleep(delay)

            except Exception as e:
                logger.error(f"Error in batch visit for profile {url_data['profile_id']}: {e}")

                results.append({
                    'profile_id': url_data['profile_id'],
                    'target_url': url_data['target_url'],
                    'status': 'error',
                    'error': str(e)
                })

                if not default_batch_params['continue_on_error']:
                    break

        successful_visits = sum(1 for r in results if r['status'] == 'success')
        failed_visits = len(results) - successful_visits

        final_result = {
            'batch_status': 'completed',
            'total_profiles': len(profile_urls),
            'successful_visits': successful_visits,
            'failed_visits': failed_visits,
            'success_rate': (successful_visits / len(profile_urls)) * 100 if profile_urls else 0,
            'individual_results': results
        }

        logger.info(f"Batch visit completed: {successful_visits}/{len(profile_urls)} successful")
        return final_result

    except Exception as e:
        logger.error(f"Error in batch visit task: {e}")
        raise


@shared_task(base=BaseTask)
def validate_yandex_maps_url(url: str) -> Dict:
    """
    Validate and extract information from Yandex Maps URL.

    Args:
        url: URL to validate

    Returns:
        Dict with validation results and extracted info
    """
    try:
        result = {
            'valid': False,
            'url': url,
            'organization_id': None,
            'coordinates': None,
            'url_type': None
        }

        if not url:
            result['error'] = "Empty URL provided"
            return result

        # Check if it's a Yandex domain
        if 'yandex' not in url.lower():
            result['error'] = "Not a Yandex Maps URL"
            return result

        parsed_url = urlparse(url)

        # Extract organization ID from various Yandex Maps URL formats
        if '/org/' in url:
            # Format: https://yandex.ru/maps/org/name/123456789/
            match = re.search(r'/org/[^/]+/(\d+)', url)
            if match:
                result['organization_id'] = match.group(1)
                result['url_type'] = 'organization'
                result['valid'] = True

        elif 'oid=' in url:
            # Format: https://yandex.ru/maps/?oid=123456789
            query_params = parse_qs(parsed_url.query)
            if 'oid' in query_params:
                result['organization_id'] = query_params['oid'][0]
                result['url_type'] = 'organization'
                result['valid'] = True

        elif 'll=' in url and 'z=' in url:
            # Coordinate-based URL
            query_params = parse_qs(parsed_url.query)
            if 'll' in query_params:
                coords = query_params['ll'][0].split(',')
                if len(coords) == 2:
                    result['coordinates'] = {
                        'longitude': float(coords[0]),
                        'latitude': float(coords[1])
                    }
                    result['url_type'] = 'coordinates'
                    result['valid'] = True

        if result['valid']:
            logger.info(f"Valid Yandex Maps URL: {result}")
        else:
            result['error'] = "Could not parse Yandex Maps URL format"
            logger.warning(f"Invalid Yandex Maps URL: {url}")

        return result

    except Exception as e:
        logger.error(f"Error validating Yandex Maps URL: {e}")
        return {
            'valid': False,
            'url': url,
            'error': str(e)
        }