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


@shared_task(base=BaseTask, bind=True, max_retries=2, default_retry_delay=30, soft_time_limit=180, time_limit=210)
def visit_yandex_maps_profile_task(self, profile_id: int, target_url: str, visit_parameters: Dict = None, task_id: int = None):
    """
    Visit a Yandex Maps profile and perform realistic interactions.

    Args:
        profile_id: Browser profile to use
        target_url: Yandex Maps profile URL
        visit_parameters: Custom parameters for the visit
        task_id: DB Task record ID for precise status tracking
    """
    browser_manager = None
    browser_id = None

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
        profile_data = profile_generator.generate_profile(profile_data_from_db['name'])

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

        # Create browser session
        browser_id = browser_manager.create_browser_session(profile_data, proxy_data)
        driver = browser_manager.active_browsers[browser_id]

        # Visit Yandex Maps profile
        start_time = time.time()

        # Navigate to target URL (use generous timeout for slow proxies)
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

        # Wait for page to load
        time.sleep(random.uniform(2, 4))

        # Check for captcha or blocks
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
        is_resource_error = any(x in error_str for x in ['connection refused', 'session not created', 'chrome not reachable', 'oom', 'out of memory'])
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
        # Note: Do NOT call cleanup_orphaned_chrome() here — it kills ALL Chrome
        # processes including those used by other concurrent tasks, causing -9 errors.
        # close_browser_session() already kills Chrome by PID for this specific session.


def detect_captcha_or_block(driver) -> bool:
    """Detect if we've been blocked or shown a captcha."""
    try:
        # First check URL — most reliable indicator
        current_url = driver.current_url.lower()
        if any(block in current_url for block in ['showcaptcha', '/captcha', 'blocked', 'verify']):
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


def handle_yandex_protection(driver, captcha_solver: CaptchaSolver) -> bool:
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
        current_url = driver.current_url.lower()
        page_source = driver.page_source
        page_source_lower = page_source.lower()
        logger.info(f"🔍 URL: {current_url[:120]}")
        
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
            return _solve_yandex_silhouette_captcha(driver, screenshot_path)
        
        # ============================================
        # YANDEX SMARTCAPTCHA (showcaptcha page OR embedded)
        # ============================================
        is_captcha_page = 'showcaptcha' in current_url or 'captcha' in current_url
        is_smartcaptcha_in_source = any(kw in page_source_lower for kw in [
            'smartcaptcha', 'checkboxcaptcha', 'checkbox-captcha', 
            'captcha-api.yandex', 'i\'m not a robot', 'я не робот',
            'advancedcaptcha', 'captcha'
        ])
        
        logger.info(f"🔍 Captcha detection: url_match={is_captcha_page}, source_match={is_smartcaptcha_in_source}")
        
        if is_captcha_page or is_smartcaptcha_in_source:
            logger.info(f"🎯 SmartCaptcha detected (url={is_captcha_page}, source={is_smartcaptcha_in_source})")
            return _solve_yandex_showcaptcha(driver, screenshot_path)
        
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
                    return _solve_yandex_showcaptcha(driver, screenshot_path)
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


def _solve_yandex_showcaptcha(driver, screenshot_path: str) -> bool:
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
        
        # ШАГ 2: Wait for reaction — either captcha resolves or image grid appears
        logger.info("⏳ Waiting for SmartCaptcha reaction...")
        
        # Save pre-click URL to detect redirect
        pre_click_url = driver.current_url
        
        # Wait up to 20 seconds for URL change (showcaptcha form submits to /checkcaptcha which redirects)
        redirected = False
        for i in range(20):
            time.sleep(1)
            try:
                new_url = driver.current_url
                if new_url != pre_click_url:
                    if 'showcaptcha' not in new_url.lower() and 'checkcaptcha' not in new_url.lower():
                        logger.info(f"🎉 Page redirected! New URL: {new_url[:100]}")
                        redirected = True
                        break
                    elif 'checkcaptcha' in new_url.lower():
                        logger.info("⏳ Form submitted, waiting for redirect...")
                        continue
            except:
                pass
            
            # At second 8 and 15, try submitting the form manually if no redirect
            if i in (8, 15):
                try:
                    form_exists = driver.execute_script("""
                        var form = document.getElementById('checkbox-captcha-form');
                        if (form) { form.submit(); return true; }
                        return false;
                    """)
                    if form_exists:
                        logger.info(f"🔄 Manually submitted captcha form (attempt at {i}s)")
                except:
                    pass
        
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
        except:
            pass
        
        # ШАГ 3: Check if image grid challenge appeared
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
            if not detect_captcha_or_block(driver):
                logger.info("🎉 Captcha resolved while waiting!")
                return True
            
            # No image grid — this is a checkbox-only captcha that didn't pass.
            # The checkbox verification failed (likely detected as bot).
            # Try refreshing and clicking again with more human-like behavior.
            logger.info("⚠️ No image grid found after checkbox click. Trying refresh + re-click...")
            
            driver.refresh()
            time.sleep(random.uniform(5, 8))
            
            # Try clicking checkbox again with longer pause
            for selector in [".CheckboxCaptcha-Button", "[class*='CheckboxCaptcha'] button"]:
                try:
                    elements = driver.find_elements(By.CSS_SELECTOR, selector)
                    for el in elements:
                        if el.is_displayed():
                            # More human-like: move slowly, pause, click
                            ActionChains(driver).move_to_element(el).pause(random.uniform(0.5, 1.5)).click().perform()
                            logger.info(f"✅ Re-clicked checkbox: {selector}")
                            break
                except:
                    continue
            
            # Wait for redirect again
            pre_url = driver.current_url
            for i in range(20):
                time.sleep(1)
                try:
                    new_url = driver.current_url
                    if new_url != pre_url and 'captcha' not in new_url.lower():
                        logger.info(f"🎉 Redirected after re-click! {new_url[:100]}")
                        time.sleep(2)
                        if not detect_captcha_or_block(driver):
                            return True
                        break
                except:
                    pass
                    
                # Check for AdvancedCaptcha appearance
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
            
            if not grid_found:
                if not detect_captcha_or_block(driver):
                    return True
                logger.warning("❌ Checkbox captcha failed — no redirect, no image grid")
                return False
        
        # ШАГ 4: Check if this is a Silhouette captcha (redirect to PazlCaptcha solver)
        try:
            page_src_check = driver.page_source.lower()
            if ('advancedcaptcha_silhouette' in page_src_check or
                'advancedcaptcha-silhouettetask' in page_src_check or
                'silhouette-container' in page_src_check or
                '/silhouette' in page_src_check):
                logger.info("🧩 Silhouette captcha detected after checkbox — switching to PazlCaptcha solver")
                return _solve_yandex_silhouette_captcha(driver, screenshot_path)
        except:
            pass
        
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


def _solve_yandex_silhouette_captcha(driver, screenshot_path: str) -> bool:
    """Solve Yandex Silhouette/PazlCaptcha using Capsola PazlCaptcha V1 API.
    
    This captcha type shows an image with silhouettes that need to be clicked in order.
    We send the full page HTML to Capsola PazlCaptcha V1 API.
    The API returns coordinates of clicks to perform.
    
    Flow:
    1. Get full page HTML
    2. Send to Capsola PazlCaptcha V1
    3. Parse result (coordinates)
    4. Click on the image at returned coordinates
    5. Submit the form
    """
    from app.config import settings
    from core.capsola_solver import create_capsola_solver
    
    try:
        capsola = create_capsola_solver(settings.capsola_api_key)
        
        # ШАГ 1: Получаем полный HTML страницы
        page_html = driver.page_source
        logger.info(f"🧩 Silhouette captcha: page HTML length = {len(page_html)}")
        
        # Save debug screenshot
        try:
            debug_ss = f"screenshots/silhouette_debug_{int(time.time())}.png"
            driver.save_screenshot(debug_ss)
            debug_html = debug_ss.replace('.png', '.html')
            with open(debug_html, 'w', encoding='utf-8') as f:
                f.write(page_html)
            logger.info(f"📄 Silhouette debug saved: {debug_html}")
        except:
            pass
        
        # ШАГ 2: Пробуем PazlCaptcha V1 (полный HTML)
        logger.info("🔄 Sending Silhouette captcha to Capsola PazlCaptcha V1...")
        result = capsola.solve_pazl_captcha_v1(page_html, max_wait=120)
        
        if not result or result.get('status') != 1:
            logger.warning(f"⚠️ PazlCaptcha V1 failed: {result}")
            
            # Fallback: попробуем PazlCaptcha V2 (image + permutations)
            logger.info("🔄 Trying PazlCaptcha V2 fallback...")
            result = _try_pazl_captcha_v2(driver, capsola)
            
            if not result or result.get('status') != 1:
                logger.error(f"❌ PazlCaptcha V2 also failed: {result}")
                # Last fallback: try as SmartCaptcha
                logger.info("🔄 Final fallback: trying as SmartCaptcha...")
                return _solve_yandex_showcaptcha(driver, screenshot_path)
        
        answer = result.get('response', '')
        logger.info(f"✅ PazlCaptcha answer: {answer}")
        
        # ШАГ 3: Парсим и применяем результат
        return _apply_silhouette_answer(driver, answer)
        
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


def _apply_silhouette_answer(driver, answer) -> bool:
    """Apply the PazlCaptcha answer by clicking at the returned coordinates on the captcha image.
    
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
                for i, (x_str, y_str) in enumerate(coord_pairs):
                    try:
                        x, y = float(x_str), float(y_str)
                        
                        # Click relative to image element with slight random offset
                        offset_x = random.randint(-1, 1)
                        offset_y = random.randint(-1, 1)
                        
                        ActionChains(driver)\
                            .move_to_element_with_offset(image_element, int(x) + offset_x, int(y) + offset_y)\
                            .pause(random.uniform(0.2, 0.5))\
                            .click()\
                            .perform()
                        
                        logger.info(f"✅ Silhouette click {i+1}: ({x:.1f}, {y:.1f})")
                        time.sleep(random.uniform(0.3, 0.8))
                    except Exception as e:
                        logger.warning(f"Click error at ({x_str}, {y_str}): {e}")
            else:
                # Try "x1,y1;x2,y2" format or "x1,y1,x2,y2" format
                parts = coords_str.replace(';', ',').split(',')
                if len(parts) >= 2 and len(parts) % 2 == 0:
                    for i in range(0, len(parts), 2):
                        try:
                            x = float(parts[i].strip())
                            y = float(parts[i+1].strip())
                            
                            ActionChains(driver)\
                                .move_to_element_with_offset(image_element, int(x), int(y))\
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
        
        # Wait for result
        time.sleep(random.uniform(5, 8))
        
        # Check if captcha resolved
        if not detect_captcha_or_block(driver):
            logger.info("🎉 Silhouette/PazlCaptcha solved successfully!")
            return True
        
        # Check if page redirected
        current_url = driver.current_url.lower()
        if 'showcaptcha' not in current_url and 'captcha' not in current_url:
            logger.info(f"🎉 Redirected away from captcha: {current_url[:100]}")
            return True
        
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
                for x_str, y_str in coord_pairs:
                    try:
                        x, y = float(x_str), float(y_str)
                        
                        if grid_element:
                            ActionChains(driver).move_to_element_with_offset(
                                grid_element, int(x), int(y)
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
        
        # Wait for result
        time.sleep(random.uniform(5, 8))
        
        if not detect_captcha_or_block(driver):
            logger.info("🎉 SmartCaptcha solved via Capsola!")
            return True
        
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

        if 'scroll' in params['actions'] and random.random() < params['scroll_probability']:
            possible_actions.append('page_scroll')

        if 'view_photos' in params['actions'] and random.random() < params['photo_click_probability']:
            possible_actions.append('view_photos')

        if 'read_reviews' in params['actions'] and random.random() < params['review_read_probability']:
            possible_actions.append('read_reviews')

        if 'click_contacts' in params['actions'] and random.random() < params['contact_click_probability']:
            possible_actions.append('click_contacts')

        if 'view_map' in params['actions'] and random.random() < params['map_interaction_probability']:
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
    """Interact with the map element."""
    try:
        map_selectors = [
            ".ymaps-map", ".map-container", "[data-bem*='map']",
            ".business-map-view", ".ymaps-glass"
        ]

        for selector in map_selectors:
            try:
                map_element = driver.find_element(By.CSS_SELECTOR, selector)
                if map_element.is_displayed():
                    # Smooth scroll to map
                    driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", map_element)
                    time.sleep(random.uniform(1, 2))

                    # Get map dimensions
                    size = map_element.size
                    if size['width'] > 100 and size['height'] > 100:
                        # Perform random clicks on map with separate ActionChains
                        for _ in range(random.randint(1, 3)):
                            x_offset = random.randint(10, size['width'] - 10)
                            y_offset = random.randint(10, size['height'] - 10)

                            # Each interaction is a separate ActionChain (not chained)
                            ActionChains(driver).move_to_element_with_offset(
                                map_element, x_offset, y_offset
                            ).pause(random.uniform(0.1, 0.3)).click().perform()

                            time.sleep(random.uniform(1, 2))

                        logger.info("Interacted with map")
                        return True

            except Exception as e:
                logger.debug(f"Error interacting with map using selector {selector}: {e}")
                continue

        return False

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