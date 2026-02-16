"""
Yandex Search click-through tasks.
Simulates organic traffic: open Yandex → search keyword → find & click target site.
"""
import os
import time
import random
import logging
import re
from typing import Dict, List, Optional
from urllib.parse import urlparse, quote_plus
from datetime import datetime

from celery import shared_task
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, WebDriverException,
    ElementClickInterceptedException, StaleElementReferenceException
)

from app.database import get_db_session
from app.models import BrowserProfile, Task
from app.models.yandex_search_target import YandexSearchTarget
from core import BrowserManager, ProxyManager, CaptchaSolver
from core.capsola_solver import create_capsola_solver
from app.config import settings
from .celery_app import BaseTask
from celery.utils.log import get_task_logger

logger = logging.getLogger(__name__)


def _update_search_task_log(task_id: int, message: str, status: str = None,
                            error: str = None, result_data: dict = None, exec_time: float = None):
    """Update search task record in DB."""
    try:
        with get_db_session() as db:
            task_obj = db.query(Task).filter(Task.id == task_id).first()
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
        logger.warning(f"Failed to update search task log: {e}")


def _human_scroll(driver, min_scrolls=2, max_scrolls=5):
    """Simulate human-like scrolling on a page."""
    num_scrolls = random.randint(min_scrolls, max_scrolls)
    for _ in range(num_scrolls):
        scroll_amount = random.randint(200, 600)
        driver.execute_script(f"window.scrollBy(0, {scroll_amount})")
        time.sleep(random.uniform(0.5, 2.0))
    # Sometimes scroll back up a bit
    if random.random() < 0.3:
        driver.execute_script(f"window.scrollBy(0, -{random.randint(100, 300)})")
        time.sleep(random.uniform(0.5, 1.0))


def _human_read_page(driver, min_time=5, max_time=15):
    """Simulate reading a page: scroll and pause."""
    read_time = random.uniform(min_time, max_time)
    start = time.time()
    while time.time() - start < read_time:
        _human_scroll(driver, 1, 2)
        time.sleep(random.uniform(1.0, 3.0))


def _find_and_click_target(driver, domain: str, max_pages: int = 3) -> dict:
    """
    Search through Yandex search results to find and click target domain.
    
    Returns:
        dict with keys: found (bool), page (int), position (int), clicked (bool)
    """
    domain_clean = domain.lower().replace('https://', '').replace('http://', '').replace('www.', '').rstrip('/')
    
    for page_num in range(1, max_pages + 1):
        logger.info(f"🔍 Scanning search results page {page_num} for domain: {domain_clean}")
        time.sleep(random.uniform(2, 4))
        
        # Collect all search result links
        result_selectors = [
            "a.OrganicTitle-Link",
            "li.serp-item a.link",
            "a.organic__url",
            "div.organic a[href]",
            "a[data-cid]",
            "h2 a[href]",
            ".serp-item a[href*='http']",
        ]
        
        all_links = []
        for selector in result_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for elem in elements:
                    try:
                        href = elem.get_attribute('href') or ''
                        text = elem.text.strip()
                        if href and ('yandex.ru/clck' in href or domain_clean in href.lower() or
                                     any(x in href for x in ['http://', 'https://'])):
                            all_links.append({
                                'element': elem,
                                'href': href,
                                'text': text,
                                'displayed': elem.is_displayed()
                            })
                    except StaleElementReferenceException:
                        continue
            except Exception:
                continue
        
        # Deduplicate by href
        seen_hrefs = set()
        unique_links = []
        for link in all_links:
            if link['href'] not in seen_hrefs and link['displayed']:
                seen_hrefs.add(link['href'])
                unique_links.append(link)
        
        logger.info(f"  Found {len(unique_links)} unique links on page {page_num}")
        
        # Log first 10 links for debugging
        for i, link in enumerate(unique_links[:10], 1):
            logger.info(f"    #{i}: {link['text'][:50]} → {link['href'][:100]}")
        
        # Search for target domain in results
        for position, link in enumerate(unique_links, 1):
            href = link['href'].lower()
            
            # Check if this link points to our target domain
            # Handle both direct URLs and Yandex redirect URLs
            if domain_clean in href:
                logger.info(f"✅ Found target at page {page_num}, position {position}: {link['href'][:100]}")
                logger.info(f"   Link text: '{link['text']}'")
                
                # Remember windows before click
                windows_before = driver.window_handles
                url_before = driver.current_url
                
                # Simulate natural behavior: scroll to element first
                try:
                    # Scroll a few results before clicking (realistic behavior)
                    if position > 2:
                        pre_scroll = random.randint(1, min(position - 1, 3))
                        for _ in range(pre_scroll):
                            driver.execute_script(f"window.scrollBy(0, {random.randint(150, 350)})")
                            time.sleep(random.uniform(0.5, 1.5))
                    
                    # Scroll to the target element
                    driver.execute_script(
                        "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});",
                        link['element']
                    )
                    time.sleep(random.uniform(0.5, 1.5))
                    
                    logger.info(f"   Clicking element...")
                    
                    # Move mouse to element and click
                    ActionChains(driver).move_to_element(link['element']).pause(
                        random.uniform(0.3, 0.8)
                    ).click().perform()
                    
                    time.sleep(random.uniform(3, 5))
                    
                except Exception as click_err:
                    logger.warning(f"   ActionChains click failed: {click_err}")
                    # Try JavaScript click as fallback
                    try:
                        driver.execute_script("arguments[0].click();", link['element'])
                        time.sleep(random.uniform(3, 5))
                    except Exception as js_err:
                        logger.warning(f"   JS click also failed: {js_err}")
                
                # Check if new tab was opened
                windows_after = driver.window_handles
                if len(windows_after) > len(windows_before):
                    new_window = [w for w in windows_after if w not in windows_before][0]
                    logger.info(f"   New tab opened, switching to it")
                    driver.switch_to.window(new_window)
                    time.sleep(2)
                
                # Check current URL
                current_url = driver.current_url.lower()
                logger.info(f"   Current URL after click: {driver.current_url[:150]}")
                
                # If still on Yandex, try direct navigation
                if domain_clean not in current_url and 'yandex.ru' in current_url:
                    logger.warning(f"   Still on Yandex after click, trying direct navigation to target")
                    # Extract actual target URL from href if it's a yandex redirect
                    target_url = link['href']
                    if 'yandex.ru/clck' in target_url:
                        # Direct URL approach
                        target_url = f"https://{domain_clean}/"
                    logger.info(f"   Navigating to: {target_url}")
                    driver.get(target_url)
                    time.sleep(random.uniform(3, 6))
                    current_url = driver.current_url.lower()
                    logger.info(f"   URL after direct nav: {driver.current_url[:150]}")
                
                clicked = domain_clean in driver.current_url.lower()
                logger.info(f"   Final result: on_target={clicked}, url={driver.current_url[:100]}")
                
                return {
                    'found': True,
                    'page': page_num,
                    'position': position,
                    'clicked': clicked,
                    'href': link['href']
                }
        
        # Target not found on this page — scroll through results naturally
        _human_scroll(driver, 2, 4)
        time.sleep(random.uniform(1, 3))
        
        # Go to next page if not last
        if page_num < max_pages:
            try:
                # Find "next page" button
                next_selectors = [
                    "a.pager__item_kind_next",
                    "a[aria-label='Следующая страница']",
                    "a.Pager-Item_type_next",
                    ".pager__item_kind_next",
                    "a[data-p]:last-child",
                ]
                
                next_btn = None
                for sel in next_selectors:
                    try:
                        elems = driver.find_elements(By.CSS_SELECTOR, sel)
                        for e in elems:
                            if e.is_displayed():
                                next_btn = e
                                break
                        if next_btn:
                            break
                    except:
                        continue
                
                if next_btn:
                    ActionChains(driver).move_to_element(next_btn).pause(
                        random.uniform(0.3, 0.7)
                    ).click().perform()
                    time.sleep(random.uniform(3, 6))
                    logger.info(f"  ➡️ Navigated to page {page_num + 1}")
                else:
                    logger.info(f"  No next page button found, stopping at page {page_num}")
                    break
            except Exception as nav_err:
                logger.warning(f"Failed to navigate to next page: {nav_err}")
                break
    
    return {'found': False, 'page': max_pages, 'position': 0, 'clicked': False}


@shared_task(base=BaseTask, bind=True, max_retries=1, default_retry_delay=30,
             soft_time_limit=1800, time_limit=2100)
def yandex_search_click_task(self, profile_id: int, target_id: int,
                             keyword: str, task_id: int = None,
                             search_params: Dict = None):
    """
    Perform a Yandex search click-through:
    1. Open yandex.ru
    2. Type keyword
    3. Find target domain in results
    4. Click on it
    5. Browse target site naturally
    
    Args:
        profile_id: Browser profile to use
        target_id: YandexSearchTarget ID
        keyword: Search keyword
        task_id: Task record ID for logging
        search_params: Additional parameters (max_pages, min_time_on_site, max_time_on_site)
    """
    browser_manager = None
    browser_id = None
    params = search_params or {}

    try:
        # Load target config
        with get_db_session() as db:
            target = db.query(YandexSearchTarget).filter(YandexSearchTarget.id == target_id).first()
            if not target:
                raise ValueError(f"Search target {target_id} not found")
            domain = target.domain
            max_pages = params.get('max_search_pages', target.max_search_pages) or 3
            min_time_on_site = params.get('min_time_on_site', target.min_time_on_site) or 30
            max_time_on_site = params.get('max_time_on_site', target.max_time_on_site) or 120

        # Load profile
        with get_db_session() as db:
            profile_obj = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
            if not profile_obj:
                raise ValueError(f"Profile {profile_id} not found")

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
            profile_obj.last_used_at = datetime.utcnow()
            db.commit()

        logger.info(f"🔍 Search click-through: profile {profile_id}, keyword '{keyword}', domain '{domain}'")
        if task_id:
            _update_search_task_log(task_id, f"🚀 Запуск: профиль {profile_data_from_db['name']}, ключ '{keyword}'", status='in_progress')

        # Initialize browser
        browser_manager = BrowserManager()

        # Chrome process guard
        try:
            import subprocess as _sp
            chrome_count = int(_sp.run(['sh', '-c', 'pgrep -c chrome || echo 0'],
                                       capture_output=True, text=True, timeout=5).stdout.strip())
            if chrome_count > 50:
                logger.warning(f"⚠️ Too many Chrome processes ({chrome_count}), cleaning up")
                from core.browser_manager import cleanup_orphaned_chrome
                cleanup_orphaned_chrome()
                time.sleep(2)
        except Exception:
            pass

        proxy_manager = ProxyManager()
        proxy_manager.load_proxies_from_db()

        # Get proxy from profile or proxy pool
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
            proxy_data = proxy_manager.get_available_proxy()

        if not proxy_data:
            logger.warning("⚠️ Нет доступных прокси — работаем без прокси (тестовый режим)")

        # Create browser session
        from core.profile_generator import ProfileGenerator
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

        browser_id = browser_manager.create_browser_session(profile_data, proxy_data)
        driver = browser_manager.active_browsers[browser_id]
        start_time = time.time()

        if task_id:
            _update_search_task_log(task_id, "🌐 Открываем Яндекс...")

        # === Step 1: Open Yandex ===
        driver.get("https://ya.ru")
        time.sleep(random.uniform(3, 6))

        # Check for captcha
        from tasks.yandex_maps import detect_captcha_or_block, handle_yandex_protection
        
        # === DETAILED CAPTCHA DIAGNOSTICS ===
        current_url_debug = driver.current_url
        page_title_debug = driver.title
        logger.info(f"📋 [DIAG] After ya.ru load: URL={current_url_debug}, Title='{page_title_debug}'")
        if task_id:
            _update_search_task_log(task_id, f"📋 URL: {current_url_debug[:120]}, Title: '{page_title_debug}'")
        
        # Save screenshot for every attempt
        try:
            diag_ss = f"screenshots/search_diag_{profile_id}_{int(time.time())}.png"
            driver.save_screenshot(diag_ss)
            logger.info(f"📸 [DIAG] Screenshot saved: {diag_ss}")
        except Exception as ss_err:
            logger.warning(f"[DIAG] Screenshot failed: {ss_err}")
        
        # Detect captcha type in detail
        page_src_lower = driver.page_source[:5000].lower()
        captcha_indicators = {
            'showcaptcha_url': 'showcaptcha' in current_url_debug.lower(),
            'captcha_url': '/captcha' in current_url_debug.lower(),
            'checkbox_captcha': 'checkboxcaptcha' in page_src_lower,
            'advanced_captcha': 'advancedcaptcha' in page_src_lower,
            'silhouette': 'silhouette' in page_src_lower,
            'kaleidoscope': 'kaleidoscope' in page_src_lower,
            'smartcaptcha': 'smartcaptcha' in page_src_lower,
            'smart_captcha_key': 'captcha-api.yandex' in page_src_lower,
            'ya_ne_robot': 'я не робот' in page_src_lower,
            'not_a_robot': 'not a robot' in page_src_lower,
            'dzen_redirect': 'dzen.ru' in current_url_debug.lower(),
        }
        detected_types = [k for k, v in captcha_indicators.items() if v]
        if detected_types:
            logger.warning(f"🔍 [DIAG] Captcha indicators found: {detected_types}")
            if task_id:
                _update_search_task_log(task_id, f"🔍 Тип капчи: {', '.join(detected_types)}")
        else:
            logger.info(f"🔍 [DIAG] No captcha indicators detected")
        
        # Save page source for later analysis
        try:
            diag_html = f"screenshots/search_diag_{profile_id}_{int(time.time())}.html"
            with open(diag_html, 'w', encoding='utf-8') as f:
                f.write(driver.page_source)
            logger.info(f"📄 [DIAG] Page source saved: {diag_html}")
        except:
            pass
        
        if detect_captcha_or_block(driver):
            logger.warning(f"🚨 Captcha detected on Yandex homepage! Types: {detected_types}")
            if task_id:
                _update_search_task_log(task_id, f"⚠️ Капча на главной Яндекса ({', '.join(detected_types) or 'unknown'}), решаем...")
            captcha_solver = CaptchaSolver()
            
            # Try solving captcha with up to 2 attempts
            solved = False
            for captcha_attempt in range(1, 3):
                solve_start = time.time()
                solved = handle_yandex_protection(driver, captcha_solver)
                solve_time = time.time() - solve_start
                logger.info(f"🔧 [DIAG] Captcha solve attempt {captcha_attempt} took {solve_time:.1f}s, result={solved}")
                logger.info(f"🔧 [DIAG] After solve: URL={driver.current_url}, Title='{driver.title}'")
                
                if solved:
                    break
                
                if captcha_attempt < 2:
                    logger.info(f"🔄 Captcha attempt {captcha_attempt} failed, refreshing for retry...")
                    if task_id:
                        _update_search_task_log(task_id, f"🔄 Попытка {captcha_attempt} не удалась, пробуем ещё раз...")
                    driver.refresh()
                    time.sleep(random.uniform(3, 5))
                    if not detect_captcha_or_block(driver):
                        logger.info("🎉 Captcha disappeared after refresh!")
                        solved = True
                        break
            
            # Save post-solve screenshot
            try:
                post_ss = f"screenshots/search_post_captcha_{profile_id}_{int(time.time())}.png"
                driver.save_screenshot(post_ss)
                logger.info(f"📸 [DIAG] Post-captcha screenshot: {post_ss}")
            except:
                pass
            
            if not solved:
                if task_id:
                    _update_search_task_log(task_id, f"❌ Не удалось решить капчу ({', '.join(detected_types)}), время: {solve_time:.1f}с", status='failed', error=f'Captcha failed: {detected_types}')
                raise Exception(f"Captcha not solved (types: {detected_types}, time: {solve_time:.1f}s)")
            if task_id:
                _update_search_task_log(task_id, f"✅ Капча решена за {solve_time:.1f}с")

        # === Step 2: Type keyword via keyboard emulation ===
        if task_id:
            _update_search_task_log(task_id, f"⌨️ Вводим запрос: '{keyword}'")

        logger.info(f"⌨️ Step 2: Typing keyword '{keyword}' into search input")
        logger.info(f"   Current URL: {driver.current_url}")
        logger.info(f"   Page title: {driver.title}")

        search_input = None
        
        # Extended list of selectors for Yandex search input
        input_selectors = [
            # Modern Yandex homepage (2024+)
            "input.search3__input",
            "input.mini-suggest__input",
            "input.HeaderDesktopForm-Input",
            "input.input__control",
            # Classic selectors
            "input#text",
            "input[name='text']",
            "textarea[name='text']",
            # Aria-based 
            "input[aria-label*='Запрос']",
            "input[aria-label*='запрос']",
            "input[aria-label*='Поиск']",
            "input[aria-label*='поиск']",
            "input[aria-label*='Search']",
            # Role-based
            "input[role='searchbox']",
            "input[role='combobox']",
            # Container-based
            "#search-input input",
            ".search2__input input",
            ".search3 input",
            "[class*='search'] input[type='text']",
            "[class*='Search'] input[type='text']",
            # Generic fallback — any visible input
            "form input[type='text']",
            "form input[type='search']",
            "form input:not([type='hidden'])",
        ]

        for selector in input_selectors:
            try:
                elems = driver.find_elements(By.CSS_SELECTOR, selector)
                for elem in elems:
                    try:
                        if elem.is_displayed() and elem.is_enabled():
                            tag = elem.tag_name
                            etype = elem.get_attribute('type') or ''
                            ename = elem.get_attribute('name') or ''
                            logger.info(f"   ✅ Found input: selector='{selector}', tag={tag}, type={etype}, name={ename}")
                            search_input = elem
                            break
                    except StaleElementReferenceException:
                        continue
                if search_input:
                    break
            except Exception:
                continue

        # If CSS selectors didn't work, try JavaScript to find the input
        if not search_input:
            logger.info("   CSS selectors failed, trying JavaScript to find search input...")
            try:
                # Try to find any input that looks like a search field via JS
                js_result = driver.execute_script("""
                    var inputs = document.querySelectorAll('input, textarea');
                    for (var i = 0; i < inputs.length; i++) {
                        var el = inputs[i];
                        var rect = el.getBoundingClientRect();
                        if (rect.width > 100 && rect.height > 10 && 
                            el.offsetParent !== null &&
                            el.type !== 'hidden' && el.type !== 'checkbox' && el.type !== 'radio') {
                            return {
                                tag: el.tagName,
                                type: el.type,
                                name: el.name,
                                id: el.id,
                                class: el.className.substring(0, 80),
                                width: rect.width,
                                height: rect.height
                            };
                        }
                    }
                    return null;
                """)
                if js_result:
                    logger.info(f"   JS found input: {js_result}")
                    # Build a specific selector from JS result
                    if js_result.get('id'):
                        search_input = driver.find_element(By.ID, js_result['id'])
                    elif js_result.get('name'):
                        search_input = driver.find_element(By.NAME, js_result['name'])
                    else:
                        # Use the JS approach to focus and we'll use ActionChains
                        driver.execute_script("""
                            var inputs = document.querySelectorAll('input, textarea');
                            for (var i = 0; i < inputs.length; i++) {
                                var el = inputs[i];
                                var rect = el.getBoundingClientRect();
                                if (rect.width > 100 && rect.height > 10 && 
                                    el.offsetParent !== null &&
                                    el.type !== 'hidden' && el.type !== 'checkbox' && el.type !== 'radio') {
                                    el.focus();
                                    el.click();
                                    return true;
                                }
                            }
                            return false;
                        """)
                        logger.info("   Focused input via JS, will type with ActionChains")
                else:
                    logger.warning("   JS also found no visible input")
            except Exception as js_err:
                logger.warning(f"   JS search failed: {js_err}")

        if search_input:
            # === Keyboard emulation: click input, then type character by character ===
            logger.info(f"   Moving to search input and clicking...")
            
            # Move mouse to input field naturally
            ActionChains(driver).move_to_element(search_input).pause(
                random.uniform(0.3, 0.7)
            ).click().perform()
            time.sleep(random.uniform(0.5, 1.0))
            
            # Clear any existing text
            search_input.clear()
            time.sleep(random.uniform(0.2, 0.5))

            # Type keyword character by character with human-like delays
            logger.info(f"   Typing '{keyword}' character by character...")
            for i, char in enumerate(keyword):
                search_input.send_keys(char)
                # Variable delay: faster in middle of word, slower at start/after space
                if char == ' ':
                    time.sleep(random.uniform(0.15, 0.4))
                elif i < 2:
                    time.sleep(random.uniform(0.1, 0.3))
                else:
                    time.sleep(random.uniform(0.04, 0.18))
            
            logger.info(f"   Keyword typed. Waiting for suggestions...")
            time.sleep(random.uniform(1.0, 2.5))
            
            # Check what's in the input now
            try:
                current_value = search_input.get_attribute('value') or ''
                logger.info(f"   Input value after typing: '{current_value}'")
            except:
                pass
            
            # Try to find and click the search button first (most reliable)
            search_submitted = False
            search_button_selectors = [
                "button.search3__button",
                "button[type='submit']",
                "button.mini-suggest__button",
                "button.HeaderDesktopForm-SubmitButton",
                "button[aria-label*='Найти']",
                "button[aria-label*='найти']",
                "button[aria-label*='Search']",
                "[class*='search'] button",
                "[class*='Search'] button",
                "form button",
            ]
            
            for btn_sel in search_button_selectors:
                try:
                    buttons = driver.find_elements(By.CSS_SELECTOR, btn_sel)
                    for btn in buttons:
                        if btn.is_displayed() and btn.is_enabled():
                            logger.info(f"   Found search button: {btn_sel}, clicking...")
                            ActionChains(driver).move_to_element(btn).pause(
                                random.uniform(0.2, 0.5)
                            ).click().perform()
                            search_submitted = True
                            break
                except:
                    continue
                if search_submitted:
                    break
            
            if not search_submitted:
                # Fallback: press Enter in the input
                logger.info("   No search button found, pressing Enter...")
                search_input.send_keys(Keys.RETURN)
            
            time.sleep(random.uniform(4, 7))
            
            logger.info(f"   Search submitted. URL: {driver.current_url[:120]}")
            
            # Verify we're on search results page
            current_url = driver.current_url.lower()
            if '/search' not in current_url and 'text=' not in current_url:
                logger.warning(f"   Not on search results page! URL: {current_url}")
                # Direct URL fallback
                logger.info(f"   Falling back to direct URL search...")
                encoded = quote_plus(keyword)
                driver.get(f"https://ya.ru/search/?text={encoded}")
                time.sleep(random.uniform(4, 7))
                logger.info(f"   After fallback URL: {driver.current_url[:120]}")
        else:
            # Last resort fallback: direct URL navigation
            logger.warning("⚠️ Could not find search input — using direct URL as fallback")
            encoded = quote_plus(keyword)
            driver.get(f"https://ya.ru/search/?text={encoded}")
            time.sleep(random.uniform(4, 7))

        # Check for captcha on search results
        search_url_debug = driver.current_url
        search_title_debug = driver.title
        logger.info(f"📋 [DIAG] Search results page: URL={search_url_debug[:150]}, Title='{search_title_debug}'")
        
        # Save search results screenshot
        try:
            search_ss = f"screenshots/search_results_{profile_id}_{int(time.time())}.png"
            driver.save_screenshot(search_ss)
            logger.info(f"📸 [DIAG] Search results screenshot: {search_ss}")
        except:
            pass
        
        # Detailed captcha check on search results
        search_src_lower = driver.page_source[:5000].lower()
        search_captcha_indicators = {
            'showcaptcha_url': 'showcaptcha' in search_url_debug.lower(),
            'captcha_url': '/captcha' in search_url_debug.lower(),
            'checkbox_captcha': 'checkboxcaptcha' in search_src_lower,
            'advanced_captcha': 'advancedcaptcha' in search_src_lower,
            'silhouette': 'silhouette' in search_src_lower,
            'kaleidoscope': 'kaleidoscope' in search_src_lower,
            'smartcaptcha': 'smartcaptcha' in search_src_lower,
        }
        search_detected = [k for k, v in search_captcha_indicators.items() if v]
        if search_detected:
            logger.warning(f"🔍 [DIAG] Search page captcha indicators: {search_detected}")
        
        if detect_captcha_or_block(driver):
            logger.warning(f"🚨 Captcha on search results! Types: {search_detected}")
            if task_id:
                _update_search_task_log(task_id, f"⚠️ Капча на выдаче ({', '.join(search_detected) or 'unknown'}), решаем...")
            
            # Save page source
            try:
                search_html = f"screenshots/search_captcha_{profile_id}_{int(time.time())}.html"
                with open(search_html, 'w', encoding='utf-8') as f:
                    f.write(driver.page_source)
            except:
                pass
            
            captcha_solver = CaptchaSolver()
            
            # Try solving with up to 2 attempts
            solved2 = False
            for search_captcha_attempt in range(1, 3):
                solve_start2 = time.time()
                solved2 = handle_yandex_protection(driver, captcha_solver)
                solve_time2 = time.time() - solve_start2
                logger.info(f"🔧 [DIAG] Search captcha solve attempt {search_captcha_attempt}: {solve_time2:.1f}s, result={solved2}")
                logger.info(f"🔧 [DIAG] After solve: URL={driver.current_url}, Title='{driver.title}'")
                
                if solved2:
                    break
                    
                if search_captcha_attempt < 2:
                    logger.info(f"🔄 Search captcha attempt {search_captcha_attempt} failed, refreshing for retry...")
                    if task_id:
                        _update_search_task_log(task_id, f"🔄 Попытка {search_captcha_attempt} не удалась, повтор...")
                    # Try reloading the search page directly
                    encoded_retry = quote_plus(keyword)
                    driver.get(f"https://ya.ru/search/?text={encoded_retry}")
                    time.sleep(random.uniform(4, 7))
                    if not detect_captcha_or_block(driver):
                        logger.info("🎉 Search page loaded without captcha on retry!")
                        solved2 = True
                        break
            
            # Post-solve screenshot
            try:
                post_ss2 = f"screenshots/search_post_captcha2_{profile_id}_{int(time.time())}.png"
                driver.save_screenshot(post_ss2)
            except:
                pass
            
            if not solved2:
                if task_id:
                    _update_search_task_log(task_id, f"❌ Не удалось решить капчу на выдаче ({', '.join(search_detected)}), {solve_time2:.1f}с", status='failed', error=f'Search captcha failed: {search_detected}')
                raise Exception(f"Captcha not solved on search results (types: {search_detected})")
            if task_id:
                _update_search_task_log(task_id, f"✅ Капча на выдаче решена за {solve_time2:.1f}с")

        if task_id:
            _update_search_task_log(task_id, f"🔎 Результаты загружены, ищем {domain}...")

        # === Step 3: Find and click target ===
        result = _find_and_click_target(driver, domain, max_pages=max_pages)

        if not result['found']:
            msg = f"❌ Сайт {domain} не найден в выдаче (проверено {max_pages} стр.)"
            logger.warning(msg)
            if task_id:
                _update_search_task_log(task_id, msg, status='failed', error='Site not found in search results')
            
            # Update target stats
            with get_db_session() as db:
                target = db.query(YandexSearchTarget).filter(YandexSearchTarget.id == target_id).first()
                if target:
                    target.total_visits = (target.total_visits or 0) + 1
                    target.failed_visits = (target.failed_visits or 0) + 1
                    target.not_found_count = (target.not_found_count or 0) + 1
                    target.today_visits = (target.today_visits or 0) + 1
                    target.today_failed = (target.today_failed or 0) + 1
                    target.last_visit_at = datetime.utcnow()
                    db.commit()
            
            return {
                'status': 'not_found',
                'profile_id': profile_id,
                'keyword': keyword,
                'domain': domain,
                'pages_checked': max_pages
            }

        if not result['clicked']:
            msg = f"⚠️ Нашли {domain} (стр.{result['page']}, поз.{result['position']}), но не удалось кликнуть"
            logger.warning(msg)
            if task_id:
                _update_search_task_log(task_id, msg, status='failed', error='Click failed')
            return {'status': 'click_failed', **result}

        if task_id:
            _update_search_task_log(task_id,
                f"✅ Нашли и перешли на {domain} (стр.{result['page']}, поз.{result['position']})")

        # === Step 4: Browse target site ===
        time.sleep(random.uniform(2, 4))
        
        logger.info(f"📖 Browsing target site: {driver.current_url[:150]}")
        
        if task_id:
            _update_search_task_log(task_id, f"📖 Просматриваем сайт: {driver.current_url[:100]}")

        # Simulate natural browsing on target site
        browse_time = random.uniform(min_time_on_site, max_time_on_site)
        browse_start = time.time()
        
        while time.time() - browse_start < browse_time:
            # Scroll naturally
            _human_scroll(driver, 1, 3)
            time.sleep(random.uniform(2, 5))
            
            # Sometimes click internal links (20% chance per cycle)
            if random.random() < 0.2:
                try:
                    internal_links = driver.find_elements(By.CSS_SELECTOR, f"a[href*='{domain_clean}'], a[href^='/'], a[href^='./']")
                    visible_links = [l for l in internal_links if l.is_displayed() and l.text.strip()]
                    if visible_links:
                        chosen = random.choice(visible_links[:10])
                        ActionChains(driver).move_to_element(chosen).pause(
                            random.uniform(0.3, 0.8)
                        ).click().perform()
                        time.sleep(random.uniform(3, 6))
                        if task_id:
                            _update_search_task_log(task_id, f"  📄 Перешли на: {driver.current_url[:80]}")
                except Exception:
                    pass
            
            # Sometimes move mouse randomly (30% chance)
            if random.random() < 0.3:
                try:
                    body = driver.find_element(By.TAG_NAME, "body")
                    x_offset = random.randint(50, 500)
                    y_offset = random.randint(50, 400)
                    ActionChains(driver).move_to_element_with_offset(body, x_offset, y_offset).perform()
                except Exception:
                    pass

        actual_browse_time = time.time() - browse_start
        total_time = time.time() - start_time

        if task_id:
            _update_search_task_log(task_id,
                f"✅ Визит завершён! На сайте {actual_browse_time:.0f}с, всего {total_time:.0f}с",
                status='completed',
                result_data={
                    'keyword': keyword,
                    'domain': domain,
                    'page_found': result['page'],
                    'position': result['position'],
                    'browse_time': round(actual_browse_time, 1),
                    'total_time': round(total_time, 1)
                },
                exec_time=total_time)

        # Update target stats — success
        with get_db_session() as db:
            target = db.query(YandexSearchTarget).filter(YandexSearchTarget.id == target_id).first()
            if target:
                target.total_visits = (target.total_visits or 0) + 1
                target.successful_visits = (target.successful_visits or 0) + 1
                target.today_visits = (target.today_visits or 0) + 1
                target.today_successful = (target.today_successful or 0) + 1
                target.last_visit_at = datetime.utcnow()
                db.commit()

        logger.info(
            f"✅ Search click-through DONE: profile {profile_id}, '{keyword}' → {domain} "
            f"(page {result['page']}, pos {result['position']}, {actual_browse_time:.0f}s on site)"
        )

        return {
            'status': 'completed',
            'profile_id': profile_id,
            'keyword': keyword,
            'domain': domain,
            'page_found': result['page'],
            'position': result['position'],
            'browse_time': round(actual_browse_time, 1),
            'total_time': round(total_time, 1)
        }

    except Exception as e:
        logger.error(f"Error in search click-through for profile {profile_id}: {e}")
        if task_id:
            _update_search_task_log(task_id, f"❌ Ошибка: {str(e)}", status='failed', error=str(e))
        
        # Update target stats — failure
        try:
            with get_db_session() as db:
                target = db.query(YandexSearchTarget).filter(YandexSearchTarget.id == target_id).first()
                if target:
                    target.total_visits = (target.total_visits or 0) + 1
                    target.failed_visits = (target.failed_visits or 0) + 1
                    target.today_visits = (target.today_visits or 0) + 1
                    target.today_failed = (target.today_failed or 0) + 1
                    target.last_visit_at = datetime.utcnow()
                    db.commit()
        except Exception:
            pass

        return {'status': 'error', 'error': str(e), 'profile_id': profile_id}

    finally:
        # Close browser
        if browser_manager and browser_id:
            try:
                browser_manager.close_browser_session(browser_id)
            except Exception as close_err:
                logger.warning(f"Error closing browser: {close_err}")


# ======================== SCHEDULER ========================

@shared_task(name='tasks.yandex_search.schedule_search_visits')
def schedule_search_visits():
    """
    Automatic scheduler for Yandex Search click-through visits.
    Runs every 5 minutes via celery beat. Checks all active YandexSearchTarget
    entries and schedules visits according to visits_per_day / intervals.
    """
    scheduler_logger = logging.getLogger(__name__ + '.scheduler')
    scheduler_logger.info("🔄 Starting Yandex Search visit scheduler")

    # Distributed lock
    try:
        import redis as _redis
        from app.config import settings as _s
        r = _redis.Redis(host=_s.redis_host, port=_s.redis_port)
        lock_key = 'scheduler:schedule_search_visits:lock'
        if not r.set(lock_key, '1', nx=True, ex=240):
            scheduler_logger.info("⏭️ Another search scheduler already running, skipping")
            return {'status': 'skipped', 'reason': 'duplicate', 'scheduled': 0}
    except Exception as le:
        scheduler_logger.warning(f"Could not acquire scheduler lock: {le}")

    # Don't flood the queue
    try:
        queue_len = r.llen('yandex_search') or 0
        if queue_len > 10:
            scheduler_logger.warning(f"⏭️ yandex_search queue already has {queue_len} tasks, skipping")
            return {'status': 'skipped', 'reason': f'queue_full ({queue_len})', 'scheduled': 0}
    except Exception as qe:
        scheduler_logger.warning(f"Could not check queue length: {qe}")

    try:
        with get_db_session() as db:
            targets = db.query(YandexSearchTarget).filter(
                YandexSearchTarget.is_active == True
            ).order_by(YandexSearchTarget.priority.desc()).all()

            if not targets:
                scheduler_logger.info("ℹ️  No active search targets found")
                return {'status': 'success', 'message': 'No active search targets', 'scheduled': 0}

            scheduler_logger.info(f"📊 Found {len(targets)} active search targets")

            # Get available warmed profiles
            all_profiles = db.query(BrowserProfile).filter(
                BrowserProfile.warmup_completed == True,
                BrowserProfile.is_active == True,
                BrowserProfile.status == 'warmed',
            ).all()

            if not all_profiles:
                scheduler_logger.warning("⚠️  No warmed profiles available")
                return {'status': 'error', 'message': 'No warmed profiles available', 'scheduled': 0}

            scheduler_logger.info(f"✅ Found {len(all_profiles)} warmed profiles")

            scheduled_count = 0
            current_time = datetime.utcnow()

            for target in targets:
                try:
                    should_visit, reason = target.should_visit_now(current_time)
                    if not should_visit:
                        scheduler_logger.info(f"⏭️  Skipping {target.domain}: {reason}")
                        continue

                    # Check daily limit
                    today_visits = target.today_visits or 0
                    if today_visits >= target.visits_per_day:
                        scheduler_logger.info(f"⏭️  {target.domain}: daily limit reached ({today_visits}/{target.visits_per_day})")
                        continue

                    visits_to_schedule = target.get_visits_needed_now(current_time)
                    remaining_today = target.visits_per_day - today_visits
                    visits_to_schedule = min(visits_to_schedule, remaining_today)

                    if visits_to_schedule <= 0:
                        scheduler_logger.info(f"⏭️  No visits needed for {target.domain}")
                        continue

                    scheduler_logger.info(f"📅 Scheduling {visits_to_schedule} search visits for: {target.domain}")

                    keywords = target.get_keywords_list()
                    if not keywords:
                        scheduler_logger.warning(f"⚠️ No keywords for {target.domain}, skipping")
                        continue

                    search_params = {
                        'max_search_pages': target.max_search_pages,
                        'min_time_on_site': target.min_time_on_site,
                        'max_time_on_site': target.max_time_on_site,
                    }

                    concurrent_visits = min(
                        visits_to_schedule,
                        target.concurrent_visits,
                        len(all_profiles)
                    )

                    random.shuffle(all_profiles)

                    for i in range(concurrent_visits):
                        profile = all_profiles[i % len(all_profiles)]
                        keyword = random.choice(keywords)

                        # Spread visits across the 5-minute window
                        delay_seconds = random.randint(0, 280)

                        # Create Task record for UI visibility
                        task_record = Task(
                            name=f"Поиск '{keyword}' → {target.domain}",
                            task_type="yandex_search",
                            status="pending",
                            target_url=f"https://yandex.ru/search/?text={keyword}",
                            profile_id=profile.id,
                            parameters={
                                'keyword': keyword,
                                'domain': target.domain,
                                'target_id': target.id,
                                **search_params
                            },
                            priority="normal",
                        )
                        db.add(task_record)
                        db.flush()

                        yandex_search_click_task.apply_async(
                            args=[profile.id, target.id, keyword, task_record.id, search_params],
                            countdown=delay_seconds,
                            queue='yandex_search'
                        )

                        scheduled_count += 1
                        scheduler_logger.info(
                            f"✅ Scheduled search visit #{i+1}/{concurrent_visits} "
                            f"for {target.domain} keyword='{keyword}' profile={profile.id} "
                            f"(delay: {delay_seconds}s)"
                        )

                    target.last_visit_at = current_time
                    db.commit()

                except Exception as e:
                    scheduler_logger.error(f"❌ Error scheduling search visits for {target.domain}: {e}", exc_info=True)
                    continue

            scheduler_logger.info(f"✅ Search scheduler completed. Scheduled {scheduled_count} visits")

            return {
                'status': 'success',
                'targets_processed': len(targets),
                'scheduled': scheduled_count,
                'timestamp': current_time.isoformat()
            }

    except Exception as e:
        scheduler_logger.error(f"❌ Search scheduler error: {e}", exc_info=True)
        return {'status': 'error', 'error': str(e)}


@shared_task(name='tasks.yandex_search.daily_search_stats_reset')
def daily_search_stats_reset():
    """
    Reset daily visit statistics for all search targets.
    Runs at midnight UTC via celery beat.
    """
    scheduler_logger = logging.getLogger(__name__ + '.scheduler')
    scheduler_logger.info("🔄 Starting daily stats reset for Yandex Search targets")

    try:
        with get_db_session() as db:
            targets = db.query(YandexSearchTarget).all()
            current_time = datetime.utcnow()

            for target in targets:
                target.today_visits = 0
                target.today_successful = 0
                target.today_failed = 0
                target.stats_reset_date = current_time

            db.commit()

            scheduler_logger.info(f"✅ Daily search reset done: {len(targets)} targets zeroed")
            return {
                'status': 'success',
                'targets_reset': len(targets),
                'timestamp': current_time.isoformat()
            }
    except Exception as e:
        scheduler_logger.error(f"❌ Daily search stats reset error: {e}", exc_info=True)
        return {'status': 'error', 'error': str(e)}
