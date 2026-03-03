"""
Yandex Search click-through tasks.
Simulates organic traffic: open Yandex → search keyword → find & click target site.
"""
import os
import time
import random
import logging
import math
import re
from typing import Dict, List, Optional
from urllib.parse import urlparse, quote_plus
from datetime import datetime, timedelta

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
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
from app.models.profile_search_visit import ProfileSearchVisit
from app.models.search_position_history import SearchPositionHistory
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


def _safe_click(driver, element, pause_min=0.3, pause_max=0.8):
    """Safely click an element: scroll into view, try ActionChains, fallback to JS click.
    
    Handles 'move target out of bounds' and 'element not interactable' by
    scrolling element into viewport first with multiple strategies.
    """
    try:
        # First scroll element into viewport
        driver.execute_script(
            "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});",
            element
        )
        time.sleep(random.uniform(0.3, 0.7))
        
        # Ensure element is visible and not covered by overlays
        driver.execute_script("""
            var elem = arguments[0];
            var rect = elem.getBoundingClientRect();
            // If element is outside viewport, scroll again
            if (rect.top < 0 || rect.bottom > window.innerHeight) {
                elem.scrollIntoView({block: 'center'});
            }
            // Remove any potential overlay/popup blocking the click
            var overlay = document.querySelector('.popup-overlay, .modal-overlay, .overlay');
            if (overlay) overlay.remove();
        """, element)
        time.sleep(random.uniform(0.2, 0.4))
        
        # Try ActionChains click (more human-like)
        ActionChains(driver).move_to_element(element).pause(
            random.uniform(pause_min, pause_max)
        ).click().perform()
    except Exception as e:
        error_msg = str(e).lower()
        # If element not interactable, try clicking a child <a> or the parent
        if 'not interactable' in error_msg or 'not clickable' in error_msg:
            try:
                # Try finding a clickable child link
                child_links = element.find_elements(By.TAG_NAME, 'a')
                if child_links:
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center'});",
                        child_links[0]
                    )
                    time.sleep(random.uniform(0.2, 0.4))
                    ActionChains(driver).move_to_element(child_links[0]).pause(
                        random.uniform(pause_min, pause_max)
                    ).click().perform()
                    return
            except Exception:
                pass
        # Fallback: JS click
        try:
            driver.execute_script("arguments[0].click();", element)
        except Exception as js_err:
            logger.warning(f"_safe_click: both ActionChains ({e}) and JS ({js_err}) failed")
            raise


def _wait_for_search_results_page(driver, keyword: str, max_wait: int = 20) -> bool:
    """After captcha solving, ensure we're on the actual search results page.
    
    The checkcaptcha intermediate page may not immediately redirect.
    This function waits for the redirect and falls back to direct navigation.
    
    Returns True if we ended up on a search results page.
    """
    for i in range(max_wait):
        current_url = driver.current_url.lower()
        # Check if we're on search results (contains /search or text= parameter)
        if '/search' in current_url or 'text=' in current_url:
            # Also verify it's not still a captcha — check only URL path, not query params
            # (utm_referrer can contain 'showcaptcha' on valid search result pages)
            url_path = current_url.split('?')[0]
            if 'showcaptcha' not in url_path and 'checkcaptcha' not in url_path:
                logger.info(f"✅ On search results page: {driver.current_url[:120]}")
                return True
        # If still on checkcaptcha, keep waiting
        url_path_check = current_url.split('?')[0]
        if 'checkcaptcha' in url_path_check:
            if i % 5 == 0:
                logger.info(f"⏳ Waiting for checkcaptcha redirect... ({i}s)")
            time.sleep(1)
            continue
        # On some other page (not search, not captcha) — might be redirected elsewhere
        url_path_other = current_url.split('?')[0]
        if 'showcaptcha' not in url_path_other and 'captcha' not in url_path_other:
            # Could be ya.ru homepage or similar — need to navigate to search
            break
        time.sleep(1)
    
    # If not on search results, navigate directly
    current_url = driver.current_url.lower()
    if '/search' not in current_url or 'text=' not in current_url:
        logger.info(f"⚠️ Not on search results after captcha, navigating directly...")
        encoded = quote_plus(keyword)
        driver.get(f"https://ya.ru/search/?text={encoded}")
        time.sleep(random.uniform(4, 7))
        
        final_url = driver.current_url.lower()
        final_url_path = final_url.split('?')[0]
        if 'showcaptcha' in final_url_path or 'checkcaptcha' in final_url_path:
            logger.warning(f"❌ Still on captcha after direct navigation: {final_url[:100]}")
            return False
        logger.info(f"✅ Navigated to search results: {driver.current_url[:120]}")
    return True


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


def _block_analytics_on_target(driver):
    """Block Yandex Metrika, Google Analytics, and other analytics/tracking scripts via CDP.
    
    This prevents analytics from loading and detecting bot behavior when visiting target sites.
    Uses Network.setBlockedURLs (CDP) to block requests before they are made.
    Also injects JS to neuter common analytics objects.
    """
    blocked_urls = [
        # Yandex Metrika
        '*mc.yandex.ru*',
        '*metrika.yandex.ru*',
        '*cdn.metrika.yandex.net*',
        '*watch.metrika*',
        '*metrica.yandex.com*',
        '*informer.yandex.ru*',
        '*webvisor*',
        '*tag.js*yandex*',
        # Google Analytics / Tag Manager
        '*google-analytics.com*',
        '*googletagmanager.com*',
        '*gtag*',
        '*analytics.google.com*',
        # Other common trackers
        '*mc.yandex.com*',
        '*top-fwz1.mail.ru*',
        '*top.mail.ru*',
        '*counter.yadro.ru*',
        '*rating.openstat.ru*',
        '*hotjar.com*',
        '*mouseflow.com*',
        '*clarity.ms*',
        '*pixel.wp.com*',
        '*connect.facebook.net*',
        '*bat.bing.com*',
    ]
    
    try:
        if hasattr(driver, 'execute_cdp_cmd'):
            # Enable network domain first
            driver.execute_cdp_cmd('Network.enable', {})
            driver.execute_cdp_cmd('Network.setBlockedURLs', {'urls': blocked_urls})
            logger.info(f"🛡️ Blocked {len(blocked_urls)} analytics/tracker URL patterns via CDP")
        else:
            logger.warning("⚠️ CDP not available, cannot block analytics URLs")
    except Exception as e:
        logger.warning(f"⚠️ Failed to set blocked URLs via CDP: {e}")
    
    # Also inject JS to neuter common analytics objects
    try:
        driver.execute_script("""
            // Neuter Yandex Metrika
            window.Ya = window.Ya || {};
            window.Ya.Metrika2 = function() { return { reachGoal: function(){}, hit: function(){}, params: function(){} }; };
            window.Ya.Metrika = window.Ya.Metrika2;
            window.ym = function() {};
            
            // Neuter Google Analytics
            window.ga = function() {};
            window.gtag = function() {};
            window.dataLayer = [];
            
            // Prevent new script elements from loading analytics
            var origCreate = document.createElement;
            document.createElement = function(tag) {
                var el = origCreate.call(document, tag);
                if (tag.toLowerCase() === 'script') {
                    var origSetAttr = el.setAttribute.bind(el);
                    var _origSrcDesc = Object.getOwnPropertyDescriptor(HTMLScriptElement.prototype, 'src');
                    Object.defineProperty(el, 'src', {
                        set: function(val) {
                            if (val && (val.indexOf('metrika') !== -1 || val.indexOf('mc.yandex') !== -1 ||
                                        val.indexOf('google-analytics') !== -1 || val.indexOf('googletagmanager') !== -1 ||
                                        val.indexOf('webvisor') !== -1 || val.indexOf('tag.js') !== -1)) {
                                return;  // Block analytics script loading
                            }
                            if (_origSrcDesc && _origSrcDesc.set) _origSrcDesc.set.call(el, val);
                        },
                        get: function() {
                            if (_origSrcDesc && _origSrcDesc.get) return _origSrcDesc.get.call(el);
                        }
                    });
                }
                return el;
            };
        """)
        logger.info("🛡️ Injected analytics neutralization JS")
    except Exception as e:
        logger.warning(f"⚠️ Failed to inject analytics neutralization JS: {e}")


def _abort_page_load_fast(driver, wait_before_abort=None):
    """Abort page loading quickly to prevent analytics scripts from executing.
    
    Strategy: wait just enough for the URL to change (redirect registered),
    then call window.stop() to kill all pending requests including Metrika.
    """
    if wait_before_abort is None:
        wait_before_abort = random.uniform(0.8, 2.0)
    
    time.sleep(wait_before_abort)
    
    try:
        driver.execute_script("window.stop();")
        logger.info(f"🛑 Page load aborted after {wait_before_abort:.1f}s to prevent analytics")
    except Exception as e:
        logger.warning(f"⚠️ window.stop() failed: {e}")
    
    # Re-inject analytics neutralization after stop
    try:
        driver.execute_script("""
            window.Ya = window.Ya || {};
            window.Ya.Metrika2 = function() { return { reachGoal: function(){}, hit: function(){}, params: function(){} }; };
            window.Ya.Metrika = window.Ya.Metrika2;
            window.ym = function() {};
            window.ga = function() {};
            window.gtag = function() {};
        """)
    except Exception:
        pass


def _human_read_page(driver, min_time=5, max_time=15):
    """Simulate reading a page: scroll and pause."""
    read_time = random.uniform(min_time, max_time)
    start = time.time()
    while time.time() - start < read_time:
        _human_scroll(driver, 1, 2)
        time.sleep(random.uniform(1.0, 3.0))


def _get_real_serp_position(driver, element) -> int:
    """Get the real SERP position of a link by finding its parent .serp-item container.
    
    Uses JavaScript to:
    1. Walk up from the link element to find the closest search result container
    2. Count all result containers on the page to determine the ordinal position
    
    Returns the 1-indexed position (1 = first result), or None if undetermined.
    """
    try:
        pos = driver.execute_script("""
            var el = arguments[0];
            
            // Walk up to find the closest search result container
            var serpItem = el.closest('li.serp-item, div.serp-item, [data-cid]');
            if (!serpItem) {
                // Try mobile selectors
                serpItem = el.closest('.Organic, .organic, .SearchSnippet');
            }
            if (!serpItem) return null;
            
            // Find all organic SERP item containers on the page
            // Use multiple selectors to handle both desktop and mobile Yandex
            var containers = document.querySelectorAll(
                'li.serp-item, div.serp-item:not(.serp-item .serp-item)'
            );
            
            // Fallback selectors if the first didn't work
            if (!containers || containers.length < 3) {
                containers = document.querySelectorAll('[data-cid]');
            }
            if (!containers || containers.length < 3) {
                containers = document.querySelectorAll('.Organic, .organic, .SearchSnippet');
            }
            
            if (!containers || containers.length === 0) return null;
            
            // Filter to only visible containers (skip hidden/ad containers)
            var visible = [];
            for (var i = 0; i < containers.length; i++) {
                var c = containers[i];
                // Skip if it's nested inside another serp-item (avoid double-counting)
                var parentSerp = c.parentElement ? c.parentElement.closest('li.serp-item, div.serp-item, [data-cid]') : null;
                if (!parentSerp && c.offsetHeight > 0) {
                    visible.push(c);
                }
            }
            
            // Find position of our item
            for (var j = 0; j < visible.length; j++) {
                if (visible[j] === serpItem || visible[j].contains(serpItem) || serpItem.contains(visible[j])) {
                    return j + 1;  // 1-indexed
                }
            }
            return null;
        """, element)
        
        if pos and isinstance(pos, (int, float)):
            return int(pos)
        return None
    except Exception as e:
        logger.warning(f"Could not determine real SERP position: {e}")
        return None


def _save_position_history(search_target_id: int, keyword: str, domain: str,
                           found: bool, page: int = None, position: int = None,
                           profile_id: int = None, task_id: int = None,
                           clicked: bool = False, browse_time: float = None,
                           serp_position: int = None):
    """Save a position check result to the history table for analytics.
    
    Args:
        serp_position: Real SERP position from JS detection (preferred over formula).
                      If provided, used as absolute_position directly.
    """
    try:
        # Use real SERP position if available, otherwise fall back to formula
        absolute_pos = None
        if serp_position:
            # Real SERP position: page-aware absolute position
            absolute_pos = (page - 1) * 10 + serp_position if page and serp_position else serp_position
        elif found and page and position:
            # Fallback: old formula (less accurate)
            absolute_pos = (page - 1) * 10 + position
        
        with get_db_session() as db:
            record = SearchPositionHistory(
                search_target_id=search_target_id,
                keyword=keyword,
                domain=domain,
                found=found,
                page=page,
                position=position,
                absolute_position=absolute_pos,
                profile_id=profile_id,
                task_id=task_id,
                clicked=clicked,
                browse_time=browse_time,
                checked_at=datetime.utcnow()
            )
            db.add(record)
            db.commit()
            logger.debug(f"📊 Position history saved: {keyword} → {domain} "
                        f"{'p' + str(page) + '#' + str(position) if found else 'NOT_FOUND'}")
    except Exception as e:
        logger.error(f"Failed to save position history: {e}")


def _calculate_keyword_clicks(db, target_id: int, keyword: str, target_success_rate: float = 100.0) -> dict:
    """
    Position-adaptive click calculation for a keyword.
    
    Takes target_success_rate (0-100%) to scale up clicks_per_day so that the
    desired number of *effective* clicks is achieved despite failed attempts.
    
    Algorithm (reduction ONLY after TOP-3):
    - New keyword (no data): 4 clicks/day (soft start)
    - Position 20-30: 4-6 clicks/day
    - Position 10-20: 8-12 clicks/day
    - Position 4-10: 12-20 clicks/day (aggressive push to TOP-3)
    - TOP-3 reached: reduce to 2 clicks/day (maintain)
    - Position drops after being TOP-3: ramp back up
    
    Returns dict with:
        clicks_per_day: int - recommended clicks for today
        phase: str - current phase (start, ramp_up, peak, ramp_down, maintain, recovery)
        current_position: float|None
        trend: str (improving, declining, stable, unknown)
        reason: str - human-readable explanation
    """
    scheduler_logger = logging.getLogger(__name__ + '.strategy')
    
    try:
        # Get last 7 days of position history for this keyword
        since_7d = datetime.utcnow() - timedelta(days=7)
        records_7d = db.query(SearchPositionHistory).filter(
            SearchPositionHistory.search_target_id == target_id,
            SearchPositionHistory.keyword == keyword,
            SearchPositionHistory.checked_at >= since_7d
        ).order_by(SearchPositionHistory.checked_at.asc()).all()
        
        # Also get last 3 days for recent trend
        since_3d = datetime.utcnow() - timedelta(days=3)
        records_3d = [r for r in records_7d if r.checked_at >= since_3d]
        
        # Count today's clicks for this keyword
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_clicks = db.query(SearchPositionHistory).filter(
            SearchPositionHistory.search_target_id == target_id,
            SearchPositionHistory.keyword == keyword,
            SearchPositionHistory.clicked == True,
            SearchPositionHistory.checked_at >= today_start
        ).count()
        
        # ── No data → soft start ──
        if not records_7d:
            return {
                "clicks_per_day": 4,
                "today_done": today_clicks,
                "phase": "start",
                "current_position": None,
                "trend": "unknown",
                "reason": "Новый ключ — мягкий старт 4 клика/день"
            }
        
        # ── Calculate positions ──
        found_7d = [r for r in records_7d if r.found and r.absolute_position]
        found_3d = [r for r in records_3d if r.found and r.absolute_position]
        
        if not found_7d:
            # Keyword checked but never found — still try regularly
            return {
                "clicks_per_day": 4,
                "today_done": today_clicks,
                "phase": "not_found",
                "current_position": None,
                "trend": "not_found",
                "reason": "Сайт не найден в выдаче — 4 попытки/день для обнаружения"
            }
        
        # Current position = average of last 3 days (or all if less)
        recent_positions = [r.absolute_position for r in found_3d] if found_3d else [r.absolute_position for r in found_7d[-5:]]
        current_pos = sum(recent_positions) / len(recent_positions)
        
        # Previous position = average of days 4-7 (or earlier data)
        earlier_positions = [r.absolute_position for r in found_7d if r.checked_at < since_3d]
        prev_pos = sum(earlier_positions) / len(earlier_positions) if earlier_positions else current_pos
        
        # ── Trend calculation ──
        if len(found_7d) < 3:
            trend = "unknown"
        else:
            diff = prev_pos - current_pos  # positive = improving (lower position = better)
            if diff > 2:
                trend = "improving"
            elif diff < -2:
                trend = "declining"
            else:
                trend = "stable"
        
        # ── Check if was ever in TOP-3 ──
        best_recent = min(recent_positions)
        was_top3 = any(r.absolute_position <= 3 for r in found_7d)
        
        # ── Position-based click calculation ──
        # Снижение ТОЛЬКО после попадания в ТОП-3
        if current_pos <= 3:
            # TOP-3 stable — maintain mode
            clicks = 2
            phase = "maintain"
            reason = f"TOP-3 (поз. {current_pos:.1f}) — поддержка 2 клика/день"
            if trend == "declining":
                clicks = 6
                phase = "recovery"
                reason = f"TOP-3 но падает (поз. {current_pos:.1f}) — усиление до 6 кликов"
        
        elif current_pos <= 10:
            # TOP-10 (включая 4-5) — агрессивное продвижение к TOP-3
            if trend == "improving":
                clicks = 16
                phase = "peak"
                reason = f"TOP-10 и растёт (поз. {current_pos:.1f}) — максимум 16 кликов (~1/час)"
            elif trend == "declining" and was_top3:
                clicks = 20
                phase = "recovery"
                reason = f"Был TOP-3, упал до {current_pos:.1f} — восстановление 20 кликов"
            elif trend == "declining":
                clicks = 14
                phase = "recovery"
                reason = f"TOP-10 но падает (поз. {current_pos:.1f}) — усиление 14 кликов"
            else:
                clicks = 12
                phase = "ramp_up"
                reason = f"TOP-10 (поз. {current_pos:.1f}) — активное продвижение 12 кликов"
        
        elif current_pos <= 20:
            # Page 2 — active promotion
            if trend == "improving":
                clicks = 12
                phase = "ramp_up"
                reason = f"Стр.2 и растёт (поз. {current_pos:.1f}) — усиление 12 кликов"
            else:
                clicks = 8
                phase = "ramp_up"
                reason = f"Стр.2 (поз. {current_pos:.1f}) — продвижение 8 кликов"
        
        elif current_pos <= 30:
            # Page 3 — moderate start
            if trend == "improving":
                clicks = 6
                phase = "ramp_up"
                reason = f"Стр.3 и растёт (поз. {current_pos:.1f}) — 6 кликов"
            else:
                clicks = 4
                phase = "start"
                reason = f"Стр.3 (поз. {current_pos:.1f}) — начальная фаза 4 клика"
        
        elif current_pos <= 40:
            # Page 4 — early detection
            if trend == "improving":
                clicks = 5
                phase = "ramp_up"
                reason = f"Стр.4 и растёт (поз. {current_pos:.1f}) — 5 кликов"
            else:
                clicks = 3
                phase = "start"
                reason = f"Стр.4 (поз. {current_pos:.1f}) — начальная фаза 3 клика"
        
        elif current_pos <= 50:
            # Page 5 — just found
            clicks = 3
            phase = "start"
            reason = f"Стр.5 (поз. {current_pos:.1f}) — старт 3 клика"
        
        else:
            # Far away — still try
            clicks = 3
            phase = "start"
            reason = f"Далеко (поз. {current_pos:.1f}) — старт 3 клика"
        
        # ── Success rate correction ──
        # Scale up clicks to compensate for failed attempts
        effective_clicks = clicks  # desired successful clicks
        if target_success_rate > 0 and target_success_rate < 95:
            rate = max(target_success_rate, 5.0) / 100.0  # floor at 5% to avoid insane numbers
            clicks = min(int(math.ceil(effective_clicks / rate)), 50)  # cap at 50 attempts/day per keyword
            reason += f" (×{1/rate:.1f} корр. на {target_success_rate:.0f}% успеха)"
        
        return {
            "clicks_per_day": clicks,
            "effective_clicks": effective_clicks,
            "today_done": today_clicks,
            "phase": phase,
            "current_position": round(current_pos, 1),
            "prev_position": round(prev_pos, 1) if prev_pos != current_pos else None,
            "trend": trend,
            "reason": reason
        }
    except Exception as e:
        scheduler_logger.error(f"Error calculating clicks for {keyword}: {e}")
        return {
            "clicks_per_day": 2,
            "effective_clicks": 2,
            "today_done": 0,
            "phase": "error",
            "current_position": None,
            "trend": "unknown",
            "reason": f"Ошибка расчёта: {e}"
        }


def _extract_organic_results_js(driver) -> list:
    """Extract organic search results using JavaScript for reliability.
    
    Returns list of dicts with: index, title, href, domain, displayed, greenUrl
    Each dict represents one organic search result (not ads, not widgets).
    """
    try:
        results = driver.execute_script("""
            var results = [];
            
            // Strategy 1: Find all serp-item containers with [data-cid] (most reliable)
            var serpItems = document.querySelectorAll('[data-cid]');
            
            for (var i = 0; i < serpItems.length; i++) {
                var item = serpItems[i];
                var rect = item.getBoundingClientRect();
                if (rect.height < 20) continue;  // Skip invisible items
                
                // Skip ads (sponsored results)  
                var isAd = item.querySelector('.label_theme_direct, .DirectBanner, [class*="Direct"], [class*="Ad_type"]');
                if (isAd) continue;
                
                // Find the main title link in this result
                var titleLink = item.querySelector(
                    'a.OrganicTitle-Link, ' +
                    'a.organic__url, ' +
                    '.OrganicTitle a[href], ' +
                    'h2 a[href], ' +
                    '.organic__title-wrapper a[href], ' +
                    'a[class*="Title"][href]'
                );
                
                if (!titleLink) {
                    // Try broader search within the item
                    var allLinks = item.querySelectorAll('a[href]');
                    for (var k = 0; k < allLinks.length; k++) {
                        var lnk = allLinks[k];
                        var href = lnk.getAttribute('href') || '';
                        var lRect = lnk.getBoundingClientRect();
                        if (lRect.height > 10 && lRect.width > 50 && 
                            (href.indexOf('http') === 0 || href.indexOf('/clck') === 0 || href.indexOf('//') === 0)) {
                            titleLink = lnk;
                            break;
                        }
                    }
                }
                
                if (!titleLink) continue;
                
                var href = titleLink.href || titleLink.getAttribute('href') || '';
                if (!href || href === '#') continue;
                
                // Extract visible domain from green URL or compute from href
                var greenUrl = '';
                var greenEl = item.querySelector(
                    '.OrganicTitle-Path, .Path, .organic__path, .typo_greenurl, ' +
                    'a[class*="greenurl"], .Organic-Path, [class*="Path"] a, ' +
                    '.OrganicUrl, [data-log-node="path"]'
                );
                if (greenEl) greenUrl = greenEl.textContent.trim();
                
                // Extract domain from href
                var domain = '';
                try {
                    if (href.indexOf('yandex.ru/clck') !== -1 || href.indexOf('ya.ru/clck') !== -1) {
                        // Yandex redirect URL — try to extract real domain from green URL
                        domain = greenUrl.replace(/^https?:\\/\\//, '').replace(/^www\\./, '').split('/')[0].split(' ')[0];
                    } else {
                        var u = new URL(href);
                        domain = u.hostname.replace(/^www\\./, '');
                    }
                } catch(e) {
                    domain = href.replace(/^https?:\\/\\//, '').replace(/^www\\./, '').split('/')[0];
                }
                
                var title = titleLink.textContent.trim() || item.querySelector('h2, [class*="Title"]')?.textContent?.trim() || '';
                
                results.push({
                    index: results.length,
                    title: title.substring(0, 100),
                    href: href,
                    domain: domain.toLowerCase(),
                    greenUrl: greenUrl.substring(0, 100),
                    displayed: rect.height > 0 && rect.width > 0
                });
            }
            
            // Strategy 2 fallback: If [data-cid] didn't find enough, try .serp-item
            if (results.length < 3) {
                var serpItems2 = document.querySelectorAll('li.serp-item, .serp-item');
                for (var j = 0; j < serpItems2.length; j++) {
                    var item2 = serpItems2[j];
                    if (item2.getAttribute('data-cid')) continue;  // Already processed
                    var rect2 = item2.getBoundingClientRect();
                    if (rect2.height < 20) continue;
                    
                    var link2 = item2.querySelector('a[href*="http"], a[href*="/clck"]');
                    if (!link2) continue;
                    
                    var href2 = link2.href || '';
                    var domain2 = '';
                    try {
                        var u2 = new URL(href2);
                        domain2 = u2.hostname.replace(/^www\\./, '');
                    } catch(e) {
                        domain2 = href2.replace(/^https?:\\/\\//, '').replace(/^www\\./, '').split('/')[0];
                    }
                    
                    results.push({
                        index: results.length,
                        title: link2.textContent.trim().substring(0, 100),
                        href: href2,
                        domain: domain2.toLowerCase(),
                        greenUrl: '',
                        displayed: true
                    });
                }
            }
            
            return results;
        """)
        return results or []
    except Exception as e:
        logger.warning(f"JS result extraction failed: {e}")
        return []


def _find_and_click_target(driver, domain: str, max_pages: int = 3) -> dict:
    """
    Search through Yandex search results to find and click target domain.
    Uses JS-based extraction for reliable result parsing.
    
    Returns:
        dict with keys: found (bool), page (int), position (int), clicked (bool)
    """
    domain_clean = domain.lower().replace('https://', '').replace('http://', '').replace('www.', '').rstrip('/')
    
    for page_num in range(1, max_pages + 1):
        logger.info(f"🔍 Scanning search results page {page_num} for domain: {domain_clean}")
        time.sleep(random.uniform(2, 4))
        
        # Save current URL to verify pagination later
        url_before_scan = driver.current_url
        
        # === Extract organic results via JavaScript ===
        organic_results = _extract_organic_results_js(driver)
        
        logger.info(f"  Found {len(organic_results)} organic results on page {page_num}")
        
        # Log first 10 results for debugging
        for res in organic_results[:10]:
            logger.info(f"    #{res['index']+1}: {res['title'][:50]} → {res['domain']} ({res['href'][:80]})")
        
        # === Human-like behavior: scroll through results before clicking ===
        # Simulate reading the SERP - scroll through a few results naturally
        if organic_results:
            num_to_browse = random.randint(1, min(3, len(organic_results)))
            for _ in range(num_to_browse):
                driver.execute_script(f"window.scrollBy(0, {random.randint(150, 400)})")
                time.sleep(random.uniform(0.8, 2.0))
        
        # === Search for target domain in results ===
        target_result = None
        target_position = 0
        
        for res in organic_results:
            position = res['index'] + 1  # 1-indexed
            
            # Check if this result matches our target domain
            res_domain = res['domain']
            # Also check green URL for redirect links
            green_domain = res.get('greenUrl', '').lower().replace('https://', '').replace('http://', '').replace('www.', '').split('/')[0].split(' ')[0]
            
            if domain_clean in res_domain or domain_clean in green_domain or \
               res_domain.endswith('.' + domain_clean) or domain_clean in res.get('href', '').lower():
                target_result = res
                target_position = position
                logger.info(f"✅ Found target '{domain_clean}' at page {page_num}, position {position}")
                logger.info(f"   Title: '{res['title']}'")
                logger.info(f"   Href: {res['href'][:150]}")
                logger.info(f"   Domain: {res_domain}, Green: {green_domain}")
                break
        
        if target_result:
            # === Scroll to the target naturally (as a human would) ===
            # Scroll past a few results before the target
            if target_position > 2:
                scroll_steps = random.randint(1, min(target_position - 1, 4))
                for _ in range(scroll_steps):
                    driver.execute_script(f"window.scrollBy(0, {random.randint(200, 450)})")
                    time.sleep(random.uniform(0.5, 1.5))
            
            time.sleep(random.uniform(0.5, 1.5))
            
            # === Find the actual clickable element ===
            click_element = None
            try:
                # Find the element by its href using JS
                click_element = driver.execute_script("""
                    var href = arguments[0];
                    // First try exact href match
                    var el = document.querySelector('a[href="' + CSS.escape(href).replace(/"/g, '\\\\"') + '"]');
                    if (el) return el;
                    
                    // Search through all [data-cid] items
                    var items = document.querySelectorAll('[data-cid]');
                    for (var i = 0; i < items.length; i++) {
                        var links = items[i].querySelectorAll('a[href]');
                        for (var j = 0; j < links.length; j++) {
                            if (links[j].href === href) return links[j];
                        }
                    }
                    return null;
                """, target_result['href'])
            except Exception as find_err:
                logger.warning(f"   Could not find element by href: {find_err}")
            
            # Fallback: find by position
            if not click_element:
                try:
                    click_element = driver.execute_script("""
                        var targetIdx = arguments[0];
                        var items = document.querySelectorAll('[data-cid]');
                        var orgIdx = 0;
                        for (var i = 0; i < items.length; i++) {
                            var item = items[i];
                            if (item.getBoundingClientRect().height < 20) continue;
                            var isAd = item.querySelector('.label_theme_direct, .DirectBanner, [class*="Direct"]');
                            if (isAd) continue;
                            if (orgIdx === targetIdx) {
                                var link = item.querySelector(
                                    'a.OrganicTitle-Link, a.organic__url, .OrganicTitle a[href], h2 a[href], a[class*="Title"][href]'
                                );
                                return link || item.querySelector('a[href*="http"]');
                            }
                            orgIdx++;
                        }
                        return null;
                    """, target_result['index'])
                except Exception as pos_err:
                    logger.warning(f"   Could not find element by position: {pos_err}")
            
            if not click_element:
                logger.warning(f"   ⚠️ Found target in DOM data but cannot get clickable element")
                return {
                    'found': True,
                    'page': page_num,
                    'position': target_position,
                    'clicked': False,
                    'href': target_result['href'],
                    'serp_position': target_position
                }
            
            # === Click the target result ===
            logger.info(f"   Clicking target element...")
            
            # Remember windows before click
            windows_before = driver.window_handles
            
            # DO NOT block analytics before click — we need Yandex's click tracking
            # (yandex.ru/clck redirect) to register the click properly
            try:
                _safe_click(driver, click_element)
            except Exception as click_err:
                logger.warning(f"   Click failed: {click_err}")
            
            # === Wait for Yandex click redirect to complete ===
            # The click goes through yandex.ru/clck → target site
            # We need to wait for this redirect to happen (click registered)
            # but abort BEFORE the target site fully loads (prevent Metrika)
            time.sleep(random.uniform(1.5, 3.0))
            
            # Check if new tab was opened
            windows_after = driver.window_handles
            if len(windows_after) > len(windows_before):
                new_window = [w for w in windows_after if w not in windows_before][0]
                logger.info(f"   New tab opened, switching to it")
                driver.switch_to.window(new_window)
                time.sleep(random.uniform(0.5, 1.5))
            
            # Check if we've left Yandex (redirect completed)
            try:
                current_url = driver.current_url.lower()
            except Exception:
                current_url = ''
            logger.info(f"   Current URL after click: {driver.current_url[:150] if current_url else 'unknown'}")
            
            # Wait a bit more if still on Yandex redirect
            if 'yandex.ru/clck' in current_url or 'ya.ru/clck' in current_url:
                logger.info(f"   Still on Yandex redirect, waiting...")
                time.sleep(random.uniform(2.0, 4.0))
                try:
                    current_url = driver.current_url.lower()
                except Exception:
                    pass
            
            # NOW block analytics and abort page load on target site
            _block_analytics_on_target(driver)
            _abort_page_load_fast(driver, wait_before_abort=random.uniform(0.3, 1.0))
            
            # Verify click success
            try:
                final_url = driver.current_url.lower()
                final_host = urlparse(final_url).netloc.lower().replace('www.', '')
            except Exception:
                final_url = ''
                final_host = ''
            
            on_yandex = ('ya.ru' in final_host or 'yandex.ru' in final_host)
            clicked = (not on_yandex) and (domain_clean in final_host or domain_clean in final_url)
            
            # If still on Yandex, the redirect might not have worked
            if on_yandex and not clicked:
                logger.warning(f"   Still on Yandex after click: {final_url[:100]}")
                # Don't do direct navigation — that defeats the purpose of organic click
                # Just report as click_failed
            
            logger.info(f"   Final result: clicked={clicked}, host={final_host}")
            
            return {
                'found': True,
                'page': page_num,
                'position': target_position,
                'clicked': clicked,
                'href': target_result['href'],
                'serp_position': target_position
            }
        
        # Target not found on this page — scroll through remaining results naturally
        _human_scroll(driver, 2, 4)
        time.sleep(random.uniform(1, 3))
        
        # === Go to next page ===
        if page_num < max_pages:
            next_page_success = False
            
            # Method 1: Direct URL manipulation (most reliable)
            try:
                current_search_url = driver.current_url
                if 'text=' in current_search_url:
                    # Build URL for next page
                    from urllib.parse import urlparse as _up, parse_qs, urlencode
                    parsed = _up(current_search_url)
                    params = parse_qs(parsed.query)
                    params['p'] = [str(page_num)]  # p=0 is page 1, p=1 is page 2, etc.
                    new_query = urlencode({k: v[0] for k, v in params.items()})
                    next_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{new_query}"
                    logger.info(f"  ➡️ Navigating to page {page_num + 1} via URL: {next_url[:120]}")
                    driver.get(next_url)
                    time.sleep(random.uniform(3, 6))
                    
                    # Verify URL changed
                    new_url = driver.current_url
                    if new_url != url_before_scan:
                        next_page_success = True
                        logger.info(f"  ✅ On page {page_num + 1}")
                    else:
                        logger.warning(f"  ⚠️ URL didn't change after pagination")
            except Exception as url_nav_err:
                logger.warning(f"  URL pagination failed: {url_nav_err}")
            
            # Method 2: Click next button (fallback)
            if not next_page_success:
                try:
                    next_selectors = [
                        "a.pager__item_kind_next",
                        "a[aria-label='Следующая страница']",
                        "a.Pager-Item_type_next",
                        ".pager__item_kind_next",
                        ".pager-load-more a",
                        "a.pager-more__button",
                        ".pager a[aria-label*='След']",
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
                        _safe_click(driver, next_btn, 0.3, 0.7)
                        time.sleep(random.uniform(3, 6))
                        # Verify we moved
                        if driver.current_url != url_before_scan:
                            next_page_success = True
                            logger.info(f"  ✅ Navigated to page {page_num + 1} via button")
                        else:
                            logger.warning(f"  ⚠️ Button click didn't change page")
                except Exception as nav_err:
                    logger.warning(f"  Button pagination failed: {nav_err}")
            
            if not next_page_success:
                logger.info(f"  ❌ Could not navigate to page {page_num + 1}, stopping")
                break
    
    return {'found': False, 'page': max_pages, 'position': 0, 'clicked': False}


@shared_task(base=BaseTask, bind=True, max_retries=1, default_retry_delay=30,
             soft_time_limit=600, time_limit=660)
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
    _profile_dir_for_cleanup = None  # Track profile dir for cleanup even if browser_id is None
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
            if chrome_count > 150:
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
            error_msg = "🚫 Нет доступных прокси! Поиск без прокси запрещён."
            logger.error(error_msg)
            if task_id:
                _update_search_task_log(task_id, error_msg, status='failed')
            return {'status': 'error', 'error': error_msg, 'profile_id': profile_id}

        # Create browser session
        from core.profile_generator import ProfileGenerator
        profile_generator = ProfileGenerator()
        
        # Detect if this is a mobile profile based on user agent
        ua_str = profile_data_from_db['user_agent']
        is_mobile = 'Mobile' in ua_str and 'Android' in ua_str
        
        profile_data = profile_generator.generate_profile(profile_data_from_db['name'], is_mobile=is_mobile)
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
        # IMPORTANT: For URL checks, only check the path (before ?) to avoid
        # false positives from utm_referrer=...showcaptcha... in search result URLs
        _url_path_lower = current_url_debug.lower().split('?')[0]
        captcha_indicators = {
            'showcaptcha_url': 'showcaptcha' in _url_path_lower,
            'captcha_url': '/captcha' in _url_path_lower,
            'checkbox_captcha': 'checkboxcaptcha' in page_src_lower,
            'advanced_captcha': 'advancedcaptcha' in page_src_lower,
            'silhouette': 'silhouette' in page_src_lower,
            'kaleidoscope': 'kaleidoscope' in page_src_lower,
            'smartcaptcha': 'smartcaptcha' in page_src_lower,
            'smart_captcha_key': 'captcha-api.yandex' in page_src_lower,
            'ya_ne_robot': 'я не робот' in page_src_lower,
            'not_a_robot': 'not a robot' in page_src_lower,
            'dzen_redirect': 'dzen.ru' in _url_path_lower,
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
            
            heavy_captcha_detected = any(t in detected_types for t in ('kaleidoscope', 'silhouette', 'advanced_captcha'))
            max_home_captcha_attempts = 1 if heavy_captcha_detected else 2

            # Try solving captcha with limited attempts
            solved = False
            for captcha_attempt in range(1, max_home_captcha_attempts + 1):
                solve_start = time.time()
                try:
                    solved = handle_yandex_protection(driver, captcha_solver, max_kaleidoscope_attempts=4)
                except Exception as _hp_err:
                    _hp_str = str(_hp_err)
                    if 'Timed out' in _hp_str or 'timeout' in _hp_str.lower():
                        logger.warning(f"⚠️ Renderer timeout in handle_yandex_protection (home captcha attempt {captcha_attempt}): {_hp_str[:200]}")
                        # Wait for renderer recovery before continuing
                        time.sleep(10)
                        try:
                            _ = driver.current_url
                            logger.info("✅ Browser responsive after timeout — checking captcha state...")
                            if not detect_captcha_or_block(driver):
                                solved = True
                            else:
                                solved = False
                        except Exception:
                            logger.error("💀 Browser unresponsive after timeout in home captcha")
                            raise Exception(f"Browser died during captcha solving: {_hp_str[:200]}")
                    else:
                        raise
                solve_time = time.time() - solve_start
                logger.info(f"🔧 [DIAG] Captcha solve attempt {captcha_attempt} took {solve_time:.1f}s, result={solved}")
                try:
                    logger.info(f"🔧 [DIAG] After solve: URL={driver.current_url}, Title='{driver.title}'")
                except Exception:
                    logger.warning("⚠️ Could not read URL/title after captcha solve")
                
                if solved:
                    break
                
                should_retry = captcha_attempt < max_home_captcha_attempts and solve_time < 90
                if should_retry:
                    logger.info(f"🔄 Captcha attempt {captcha_attempt} failed, refreshing for retry...")
                    if task_id:
                        _update_search_task_log(task_id, f"🔄 Попытка {captcha_attempt} не удалась, пробуем ещё раз...")
                    try:
                        driver.refresh()
                    except Exception as _ref_err:
                        if 'Timed out' in str(_ref_err) or 'timeout' in str(_ref_err).lower():
                            logger.warning("⚠️ Renderer timeout during refresh — waiting...")
                            time.sleep(10)
                        else:
                            raise
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
            
            # Wait for redirect to complete after captcha solve (checkcaptcha → actual page)
            for _redir_wait in range(15):
                try:
                    _cur = driver.current_url.lower()
                except Exception as _rw_err:
                    if 'Timed out' in str(_rw_err) or 'timeout' in str(_rw_err).lower():
                        logger.warning(f"⚠️ Renderer timeout waiting for redirect — waiting for recovery...")
                        time.sleep(10)
                        continue
                    break
                # Check only URL path, not query params (utm_referrer may contain showcaptcha)
                _cur_path = _cur.split('?')[0]
                if 'checkcaptcha' not in _cur_path and 'showcaptcha' not in _cur_path:
                    break
                time.sleep(1)
            try:
                logger.info(f"📋 After captcha redirect: URL={driver.current_url[:120]}")
            except Exception:
                logger.warning("⚠️ Could not read URL after captcha redirect")

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
            # Mobile Yandex (touch version)
            "input.HeaderPhone-Input",
            "input.HeaderMobileForm-Input",
            "input.search-input__input",
            "input.mini-suggest__input",
            "input.input__control[name='text']",
            ".search-arrow input",
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
            
            typing_succeeded = False
            try:
                # Move mouse to input field naturally
                _safe_click(driver, search_input, 0.3, 0.7)
                time.sleep(random.uniform(0.5, 1.0))
                
                # Clear any existing text
                try:
                    search_input.clear()
                except Exception:
                    driver.execute_script("arguments[0].value = '';", search_input)
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
                typing_succeeded = True
            except Exception as type_err:
                logger.warning(f"   send_keys failed ({type_err}), falling back to JS typing")
                # Fallback: type via JavaScript
                try:
                    driver.execute_script("""
                        var input = arguments[0];
                        var text = arguments[1];
                        input.focus();
                        input.value = '';
                        // Use native input setter to trigger React/Vue events
                        var nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                        nativeInputValueSetter.call(input, text);
                        input.dispatchEvent(new Event('input', { bubbles: true }));
                        input.dispatchEvent(new Event('change', { bubbles: true }));
                    """, search_input, keyword)
                    typing_succeeded = True
                    logger.info(f"   JS typing succeeded")
                except Exception as js_type_err:
                    logger.warning(f"   JS typing also failed: {js_type_err}")
            
            if typing_succeeded:
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
                # Mobile Yandex search buttons
                "button.HeaderPhone-SubmitButton",
                "button.HeaderMobileForm-SubmitButton",
                ".search-arrow__button",
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
                            _safe_click(driver, btn, 0.2, 0.5)
                            search_submitted = True
                            break
                except:
                    continue
                if search_submitted:
                    break
            
            if not search_submitted:
                # Fallback: press Enter in the input or submit form via JS
                logger.info("   No search button found, pressing Enter...")
                try:
                    search_input.send_keys(Keys.RETURN)
                except Exception:
                    # JS fallback for submit
                    logger.info("   send_keys(RETURN) failed, submitting form via JS...")
                    driver.execute_script("""
                        var input = arguments[0];
                        var form = input.closest('form');
                        if (form) {
                            form.submit();
                        } else {
                            input.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true}));
                        }
                    """, search_input)
            
            time.sleep(random.uniform(4, 7))
            
            logger.info(f"   Search submitted. URL: {driver.current_url[:120]}")
            
            # Verify we're on search results page
            current_url = driver.current_url.lower()
            if '/search' not in current_url and 'text=' not in current_url:
                logger.warning(f"   Not on search results page! URL: {current_url}")
                # If we landed on a captcha page, DON'T try direct URL fallback —
                # it will just redirect to captcha again and hang for 300s.
                # Let the captcha handling code below deal with it.
                # Check only URL path for captcha indicators (not query params like utm_referrer)
                _cur_url_path = current_url.split('?')[0]
                if 'showcaptcha' in _cur_url_path or 'captchafast' in _cur_url_path or 'checkcaptcha' in _cur_url_path:
                    logger.info(f"   Captcha page detected — skipping direct URL fallback, will solve captcha below")
                else:
                    # Direct URL fallback (only for non-captcha redirects)
                    logger.info(f"   Falling back to direct URL search...")
                    encoded = quote_plus(keyword)
                    try:
                        driver.set_page_load_timeout(30)
                        driver.get(f"https://ya.ru/search/?text={encoded}")
                        time.sleep(random.uniform(4, 7))
                        logger.info(f"   After fallback URL: {driver.current_url[:120]}")
                    except TimeoutException:
                        logger.warning(f"   Fallback URL timed out, continuing with current page...")
                    except Exception as e:
                        logger.warning(f"   Fallback URL error: {e}")
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
            # Check only URL path for captcha (not query params like utm_referrer)
            'showcaptcha_url': 'showcaptcha' in search_url_debug.lower().split('?')[0],
            'captcha_url': '/captcha' in search_url_debug.lower().split('?')[0],
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
            
            heavy_search_captcha = any(t in search_detected for t in ('kaleidoscope', 'silhouette', 'advanced_captcha'))
            max_search_captcha_attempts = 1 if heavy_search_captcha else 2

            # Try solving with limited attempts
            solved2 = False
            for search_captcha_attempt in range(1, max_search_captcha_attempts + 1):
                solve_start2 = time.time()
                try:
                    solved2 = handle_yandex_protection(driver, captcha_solver, max_kaleidoscope_attempts=4)
                except Exception as _hp2_err:
                    _hp2_str = str(_hp2_err)
                    if 'Timed out' in _hp2_str or 'timeout' in _hp2_str.lower():
                        logger.warning(f"⚠️ Renderer timeout in handle_yandex_protection (search captcha attempt {search_captcha_attempt}): {_hp2_str[:200]}")
                        time.sleep(10)
                        try:
                            _ = driver.current_url
                            logger.info("✅ Browser responsive after timeout — checking captcha state...")
                            if not detect_captcha_or_block(driver):
                                solved2 = True
                            else:
                                solved2 = False
                        except Exception:
                            logger.error("💀 Browser unresponsive after timeout in search captcha")
                            raise Exception(f"Browser died during search captcha solving: {_hp2_str[:200]}")
                    else:
                        raise
                solve_time2 = time.time() - solve_start2
                logger.info(f"🔧 [DIAG] Search captcha solve attempt {search_captcha_attempt}: {solve_time2:.1f}s, result={solved2}")
                try:
                    logger.info(f"🔧 [DIAG] After solve: URL={driver.current_url}, Title='{driver.title}'")
                except Exception:
                    logger.warning("⚠️ Could not read URL/title after search captcha solve")
                
                if solved2:
                    break
                    
                should_retry2 = search_captcha_attempt < max_search_captcha_attempts and solve_time2 < 90
                if should_retry2:
                    logger.info(f"🔄 Search captcha attempt {search_captcha_attempt} failed, refreshing for retry...")
                    if task_id:
                        _update_search_task_log(task_id, f"🔄 Попытка {search_captcha_attempt} не удалась, повтор...")
                    # Try reloading the search page directly (with timeout protection)
                    encoded_retry = quote_plus(keyword)
                    try:
                        driver.set_page_load_timeout(30)
                        driver.get(f"https://ya.ru/search/?text={encoded_retry}")
                        time.sleep(random.uniform(4, 7))
                    except TimeoutException:
                        logger.warning("⚠️ Search page reload timed out")
                    except Exception as e:
                        logger.warning(f"⚠️ Search page reload error: {e}")
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
            
            # Wait for actual redirect to search results page after captcha solve
            if not _wait_for_search_results_page(driver, keyword, max_wait=15):
                logger.warning("⚠️ Could not reach search results after captcha solve")
                if task_id:
                    _update_search_task_log(task_id, "⚠️ Не удалось перейти к результатам после капчи")
                # Check for captcha again
                if detect_captcha_or_block(driver):
                    raise Exception("Captcha reappeared after solve")

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
            
            # Save position history: not found
            _save_position_history(
                search_target_id=target_id, keyword=keyword, domain=domain,
                found=False, page=max_pages, position=0,
                profile_id=profile_id, task_id=task_id, clicked=False
            )
            
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
            # Save position history: found but click failed
            _save_position_history(
                search_target_id=target_id, keyword=keyword, domain=domain,
                found=True, page=result['page'], position=result['position'],
                profile_id=profile_id, task_id=task_id, clicked=False,
                serp_position=result.get('serp_position')
            )
            return {'status': 'click_failed', **result}

        # === Step 4: Click completed — finish immediately (no site browsing) ===
        total_time = time.time() - start_time
        actual_browse_time = 0  # No site browsing in this mode

        if task_id:
            _update_search_task_log(task_id,
                f"✅ Клик выполнен! {domain} (стр.{result['page']}, поз.{result['position']}), всего {total_time:.0f}с",
                status='completed',
                result_data={
                    'keyword': keyword,
                    'domain': domain,
                    'page_found': result['page'],
                    'position': result['position'],
                    'browse_time': 0,
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

                # Record profile-search visit (one profile clicks one site only once)
                existing_visit = db.query(ProfileSearchVisit).filter(
                    ProfileSearchVisit.profile_id == profile_id,
                    ProfileSearchVisit.search_target_id == target_id
                ).first()
                if not existing_visit:
                    visit_record = ProfileSearchVisit(
                        profile_id=profile_id,
                        search_target_id=target_id,
                        keyword=keyword,
                        status="completed",
                        visited_at=datetime.utcnow()
                    )
                    db.add(visit_record)
                else:
                    existing_visit.status = "completed"
                    existing_visit.visited_at = datetime.utcnow()

                db.commit()
                logger.info(f"✅ Recorded profile-search visit: profile={profile_id}, search_target={target_id}")

        logger.info(
            f"✅ Search click-through DONE: profile {profile_id}, '{keyword}' → {domain} "
            f"(page {result['page']}, pos {result['position']}, {actual_browse_time:.0f}s on site)"
        )

        # Save position history: success
        _save_position_history(
            search_target_id=target_id, keyword=keyword, domain=domain,
            found=True, page=result['page'], position=result['position'],
            profile_id=profile_id, task_id=task_id, clicked=True,
            browse_time=round(actual_browse_time, 1),
            serp_position=result.get('serp_position')
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

    except SoftTimeLimitExceeded:
        logger.error(f"⏰ Soft time limit exceeded for search task profile {profile_id}, cleaning up Chrome...")
        if task_id:
            _update_search_task_log(task_id, "⏰ Превышено время выполнения задачи", status='failed', error='SoftTimeLimitExceeded')
        raise

    except Exception as e:
        error_str = str(e)
        logger.error(f"Error in search click-through for profile {profile_id}: {e}")
        
        # Retry on proxy tunnel failures (ERR_TUNNEL_CONNECTION_FAILED)
        if 'ERR_TUNNEL_CONNECTION_FAILED' in error_str or 'ERR_PROXY_CONNECTION_FAILED' in error_str:
            logger.warning("🔄 Proxy tunnel failed — will retry with different proxy")
            if task_id:
                _update_search_task_log(task_id, f"🔄 Прокси не работает, повторяем...")
            # Close current browser before retry
            if browser_manager and browser_id:
                try:
                    browser_manager.close_browser_session(browser_id)
                    browser_id = None
                except Exception:
                    pass
            try:
                raise self.retry(exc=e, countdown=10, max_retries=2)
            except self.MaxRetriesExceededError:
                logger.error("Max retries exceeded for proxy tunnel failure")
        
        if task_id:
            _update_search_task_log(task_id, f"❌ Ошибка: {error_str[:200]}", status='failed', error=error_str[:500])
        
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
        elif _profile_dir_for_cleanup:
            # browser_id is None — Chrome failed to start but may have left orphans.
            try:
                if browser_manager and hasattr(browser_manager, '_kill_chrome_by_profile_dir'):
                    browser_manager._kill_chrome_by_profile_dir(_profile_dir_for_cleanup)
                else:
                    import subprocess as _sp
                    _sp.run(['pkill', '-9', '-f', _profile_dir_for_cleanup], capture_output=True, timeout=5)
            except Exception as cleanup_err:
                logger.warning(f"Cleanup by profile dir failed: {cleanup_err}")


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

    # Don't flood the queue — check both Redis queue AND active DB tasks
    try:
        queue_len = r.llen('yandex_search') or 0
        if queue_len > 100:
            scheduler_logger.warning(f"⏭️ yandex_search queue already has {queue_len} tasks, skipping")
            return {'status': 'skipped', 'reason': f'queue_full ({queue_len})', 'scheduled': 0}
    except Exception as qe:
        scheduler_logger.warning(f"Could not check queue length: {qe}")

    # ── Limit total concurrent tasks to avoid overloading proxies ──
    MAX_CONCURRENT_SEARCH_TASKS = 40
    try:
        with get_db_session() as db:
            active_count = db.query(Task).filter(
                Task.task_type == 'yandex_search',
                Task.status.in_(['in_progress', 'pending']),
            ).count()
            if active_count >= MAX_CONCURRENT_SEARCH_TASKS:
                scheduler_logger.info(
                    f"⏭️ Already {active_count} active search tasks (limit={MAX_CONCURRENT_SEARCH_TASKS}), skipping"
                )
                # Still run cleanup below, but skip scheduling
                pass
    except Exception as ce:
        active_count = 0
        scheduler_logger.warning(f"Could not check active task count: {ce}")

    # ── Cleanup zombie tasks ──
    # When workers restart or tasks hit SoftTimeLimitExceeded, DB records may
    # stay as 'in_progress' forever.
    # IMPORTANT: do NOT aggressively fail 'pending' tasks by created_at — they
    # may simply be waiting in the queue.
    try:
        with get_db_session() as db:
            now = datetime.utcnow()

            # If a task is truly running, started_at must exist.
            # Give it a grace period slightly above the task soft time limit.
            in_progress_cutoff = now - timedelta(minutes=12)
            stale_in_progress = db.query(Task).filter(
                Task.task_type == 'yandex_search',
                Task.status == 'in_progress',
                Task.started_at.isnot(None),
                Task.started_at < in_progress_cutoff,
            ).all()

            # Pending tasks may sit in Redis if workers are busy. Only clean them
            # if they are REALLY old (e.g., worker was down).
            pending_cutoff = now - timedelta(minutes=90)
            stale_pending = db.query(Task).filter(
                Task.task_type == 'yandex_search',
                Task.status == 'pending',
                Task.created_at < pending_cutoff,
            ).all()

            stale_tasks = list(stale_in_progress) + list(stale_pending)
            if stale_tasks:
                scheduler_logger.info(
                    f"🧹 Cleaning up stale tasks: in_progress>{in_progress_cutoff.isoformat()} ({len(stale_in_progress)}), "
                    f"pending>{pending_cutoff.isoformat()} ({len(stale_pending)})"
                )
                for st in stale_tasks:
                    old_status = st.status
                    st.status = 'failed'
                    if old_status == 'pending':
                        st.error_message = st.error_message or 'Task stuck in pending (auto-cleanup)'
                        st.add_log("🧹 Auto-cleaned: stuck in 'pending' for too long")
                    else:
                        st.error_message = st.error_message or 'Task timed out or worker restarted (auto-cleanup)'
                        st.add_log("🧹 Auto-cleaned: stuck in 'in_progress' past expected runtime")
                    st.completed_at = now
                db.commit()
                scheduler_logger.info(f"✅ Cleaned up {len(stale_tasks)} stale tasks")
    except Exception as cleanup_err:
        scheduler_logger.warning(f"Could not clean up stale tasks: {cleanup_err}")

    # Recompute active count AFTER cleanup; previous value may be stale.
    try:
        with get_db_session() as db:
            active_count = db.query(Task).filter(
                Task.task_type == 'yandex_search',
                Task.status.in_(['in_progress', 'pending']),
            ).count()
    except Exception as ce2:
        scheduler_logger.warning(f"Could not re-check active task count after cleanup: {ce2}")

    # After cleanup, re-check active count and skip scheduling if still too many
    if active_count >= MAX_CONCURRENT_SEARCH_TASKS:
        return {
            'status': 'skipped',
            'reason': f'too_many_active ({active_count}/{MAX_CONCURRENT_SEARCH_TASKS})',
            'scheduled': 0
        }

    try:
        with get_db_session() as db:
            targets = db.query(YandexSearchTarget).filter(
                YandexSearchTarget.is_active == True
            ).order_by(YandexSearchTarget.priority.desc()).all()

            if not targets:
                scheduler_logger.info("ℹ️  No active search targets found")
                return {'status': 'success', 'message': 'No active search targets', 'scheduled': 0}

            scheduler_logger.info(f"📊 Found {len(targets)} active search targets")

            # Get available warmed profiles (load IDs eagerly to avoid stale references after commits)
            all_profiles_q = db.query(BrowserProfile.id).filter(
                BrowserProfile.warmup_completed == True,
                BrowserProfile.is_active == True,
                BrowserProfile.status == 'warmed',
            ).all()
            all_profile_ids = [row[0] for row in all_profiles_q]

            if not all_profile_ids:
                scheduler_logger.warning("⚠️  No warmed profiles available")
                return {'status': 'error', 'message': 'No warmed profiles available', 'scheduled': 0}

            scheduler_logger.info(f"✅ Found {len(all_profile_ids)} warmed profiles")

            scheduled_count = 0
            # How many more tasks we can schedule before hitting the global limit
            slots_available = MAX_CONCURRENT_SEARCH_TASKS - active_count
            current_time = datetime.utcnow()

            # ═══ Phase 1: Gather budget data for ALL targets ═══
            target_schedule_data = []
            for target in targets:
                try:
                    should_visit, reason = target.should_visit_now(current_time)
                    if not should_visit:
                        scheduler_logger.info(f"⏭️  Skipping {target.domain}: {reason}")
                        continue

                    keywords = target.get_keywords_list()
                    if not keywords:
                        scheduler_logger.warning(f"⚠️ No keywords for {target.domain}, skipping")
                        continue

                    # Calculate click budget for each keyword based on position history
                    sr = target.success_rate if target.total_visits >= 10 else 100.0
                    keyword_budgets = []
                    total_budget = 0
                    for kw in keywords:
                        kw_calc = _calculate_keyword_clicks(db, target.id, kw, target_success_rate=sr)
                        remaining = max(0, kw_calc["clicks_per_day"] - kw_calc["today_done"])
                        keyword_budgets.append({
                            "keyword": kw,
                            "clicks_per_day": kw_calc["clicks_per_day"],
                            "today_done": kw_calc["today_done"],
                            "remaining": remaining,
                            "phase": kw_calc["phase"],
                            "position": kw_calc.get("current_position"),
                            "reason": kw_calc["reason"],
                        })
                        total_budget += remaining

                    if total_budget <= 0:
                        # Keyword-level budgets exhausted, but check if target's
                        # visits_per_day still demands more clicks
                        today_done_total = sum(kb["today_done"] for kb in keyword_budgets)
                        daily_target = max(target.visits_per_day, 1)
                        remaining_daily_target = max(0, daily_target - today_done_total)

                        if remaining_daily_target <= 0:
                            scheduler_logger.info(
                                f"⏭️  {target.domain}: all keywords at daily limit "
                                f"and daily target met ({today_done_total}/{daily_target})"
                            )
                            continue

                        # Target still needs visits — redistribute budget evenly
                        # across all keywords
                        scheduler_logger.info(
                            f"📈 {target.domain}: keyword budgets exhausted but daily target "
                            f"not met ({today_done_total}/{daily_target}), redistributing {remaining_daily_target} clicks"
                        )
                        per_kw = max(1, remaining_daily_target // max(len(keyword_budgets), 1))
                        total_budget = 0
                        for kb in keyword_budgets:
                            kb["remaining"] = per_kw
                            kb["clicks_per_day"] = kb["today_done"] + per_kw
                            total_budget += per_kw

                    # ── Scale up keyword budgets to meet visits_per_day target ──
                    # The position-adaptive algorithm determines per-keyword clicks,
                    # but if the sum is far below the target's visits_per_day, we
                    # scale up proportionally so the daily goal is achievable.
                    today_done_total = sum(kb["today_done"] for kb in keyword_budgets)
                    daily_target = max(target.visits_per_day, 1)
                    remaining_daily_target = max(0, daily_target - today_done_total)

                    if remaining_daily_target > 0 and total_budget < remaining_daily_target:
                        scale_factor = remaining_daily_target / total_budget if total_budget > 0 else 1.0
                        # Cap scale factor to avoid extremely aggressive overrides
                        scale_factor = min(scale_factor, 5.0)
                        if scale_factor > 1.1:
                            scheduler_logger.info(
                                f"📈 {target.domain}: scaling keyword budgets ×{scale_factor:.1f} "
                                f"(position-budget={total_budget}, daily_target_remaining={remaining_daily_target})"
                            )
                            total_budget = 0
                            for kb in keyword_budgets:
                                old_remaining = kb["remaining"]
                                if old_remaining > 0:
                                    scaled = int(math.ceil(old_remaining * scale_factor))
                                    kb["clicks_per_day"] = kb["today_done"] + scaled
                                    kb["remaining"] = scaled
                                    total_budget += scaled
                                else:
                                    total_budget += 0

                    # Sort: zero-click keywords first, then by remaining budget desc
                    candidates = [kb for kb in keyword_budgets if kb["remaining"] > 0]
                    if not candidates:
                        continue
                    zero_click = [kb for kb in candidates if kb["today_done"] == 0]
                    has_clicks = [kb for kb in candidates if kb["today_done"] > 0]
                    zero_click.sort(key=lambda x: -x["remaining"])
                    has_clicks.sort(key=lambda x: -x["remaining"])
                    ordered_candidates = zero_click + has_clicks

                    # Log strategy summary
                    summary_parts = []
                    for kb in keyword_budgets[:5]:
                        pos_str = f"поз.{kb['position']}" if kb['position'] else "?"
                        summary_parts.append(f"{kb['keyword'][:20]}({pos_str}, {kb['today_done']}/{kb['clicks_per_day']}, {kb['phase']})")
                    scheduler_logger.info(
                        f"📊 {target.domain} strategy: total_budget={total_budget}, "
                        f"keywords: {', '.join(summary_parts)}"
                    )

                    # ── Profile selection with reuse support ──
                    # Prefer profiles that never visited this target.
                    # If all profiles have visited, allow reuse — pick profiles
                    # with the least recent visit (cooldown-based round-robin).
                    PROFILE_REUSE_COOLDOWN_HOURS = 4  # min hours before reusing a profile for same target
                    try:
                        from sqlalchemy import func as sa_func
                        # Get last visit time per profile for this target
                        visit_rows = db.query(
                            ProfileSearchVisit.profile_id,
                            ProfileSearchVisit.visited_at
                        ).filter(
                            ProfileSearchVisit.search_target_id == target.id,
                            ProfileSearchVisit.status == "completed"
                        ).all()

                        visited_map = {row[0]: row[1] for row in visit_rows}
                    except Exception as ve:
                        scheduler_logger.warning(f"Could not query visited search profiles: {ve}")
                        visited_map = {}

                    cooldown_cutoff = current_time - timedelta(hours=PROFILE_REUSE_COOLDOWN_HOURS)
                    # Tier 1: never visited this target
                    never_visited = [pid for pid in all_profile_ids if pid not in visited_map]
                    # Tier 2: visited but cooldown elapsed (sorted by oldest visit first)
                    reusable = [pid for pid in all_profile_ids
                                if pid in visited_map and visited_map[pid] and visited_map[pid] < cooldown_cutoff]
                    reusable.sort(key=lambda pid: visited_map[pid])  # oldest first
                    # Tier 3: recently visited (last resort)
                    recent = [pid for pid in all_profile_ids
                              if pid in visited_map and (not visited_map[pid] or visited_map[pid] >= cooldown_cutoff)]
                    recent.sort(key=lambda pid: visited_map.get(pid) or datetime.min)

                    random.shuffle(never_visited)
                    available_ids = never_visited + reusable + recent

                    if not available_ids:
                        scheduler_logger.warning(
                            f"⚠️ No profiles at all for {target.domain}, skipping"
                        )
                        continue

                    scheduler_logger.info(
                        f"🔄 {target.domain}: {len(never_visited)} fresh + "
                        f"{len(reusable)} reusable + {len(recent)} recent = "
                        f"{len(available_ids)} profiles"
                    )

                    target_schedule_data.append({
                        'target': target,
                        'total_budget': total_budget,
                        'candidates': ordered_candidates,
                        'available_ids': available_ids,
                    })

                except Exception as e:
                    scheduler_logger.error(f"❌ Error computing budget for {target.domain}: {e}", exc_info=True)
                    continue

            if not target_schedule_data:
                scheduler_logger.info("ℹ️  No targets with remaining budget")
                return {'status': 'success', 'message': 'No targets need visits', 'scheduled': 0}

            # ═══ Phase 2: Proportional scheduling — multiple tasks per target ═══
            grand_total = sum(td['total_budget'] for td in target_schedule_data)
            # Sort by budget descending — highest demand first
            target_schedule_data.sort(key=lambda x: -x['total_budget'])

            scheduler_logger.info(
                f"📊 Budget allocation: grand_total={grand_total}, slots={slots_available}, "
                f"targets={len(target_schedule_data)}"
            )

            for td in target_schedule_data:
                if scheduled_count >= slots_available:
                    scheduler_logger.info(f"⏭️ Global concurrency limit reached ({MAX_CONCURRENT_SEARCH_TASKS}), stopping")
                    break

                target = td['target']
                candidates = td['candidates']
                available_ids = td['available_ids']

                # Proportional slot allocation: target gets share based on budget
                proportion = td['total_budget'] / grand_total if grand_total > 0 else 1.0 / len(target_schedule_data)
                target_slots = max(1, round(slots_available * proportion))
                # Cap: don't exceed remaining slots, available keywords, or available profiles
                target_slots = min(target_slots, slots_available - scheduled_count, len(candidates), len(available_ids))

                scheduler_logger.info(
                    f"🎯 {target.domain}: budget={td['total_budget']}, "
                    f"allocating {target_slots} tasks (proportion={proportion:.1%})"
                )

                search_params = {
                    'max_search_pages': target.max_search_pages,
                    'min_time_on_site': target.min_time_on_site,
                    'max_time_on_site': target.max_time_on_site,
                }

                for i in range(target_slots):
                    if scheduled_count >= slots_available:
                        break

                    try:
                        chosen = candidates[i]
                        keyword = chosen['keyword']
                        profile_id = available_ids[i % len(available_ids)]

                        # Spread visits evenly across the 5-minute window
                        # Each task gets its own time slot to avoid CPU spikes
                        total_tasks_planned = min(slots_available, sum(
                            min(max(1, round(slots_available * (td2['total_budget'] / grand_total))),
                                len(td2['candidates']), len(td2['available_ids']))
                            for td2 in target_schedule_data
                        ))
                        slot_width = 280 // max(total_tasks_planned, 1)
                        delay_seconds = scheduled_count * slot_width + random.randint(0, max(slot_width - 5, 1))

                        scheduler_logger.info(
                            f"  📝 [{i+1}/{target_slots}] {target.domain} keyword='{keyword}' "
                            f"({chosen['reason']}, done {chosen['today_done']}/{chosen['clicks_per_day']})"
                        )

                        # Create Task record for UI visibility
                        task_record = Task(
                            name=f"Поиск '{keyword}' → {target.domain}",
                            task_type="yandex_search",
                            status="pending",
                            target_url=f"https://yandex.ru/search/?text={keyword}",
                            profile_id=profile_id,
                            parameters={
                                'keyword': keyword,
                                'domain': target.domain,
                                'target_id': target.id,
                                'phase': chosen['phase'],
                                'position': chosen.get('position'),
                                'clicks_budget': chosen['clicks_per_day'],
                                'clicks_done': chosen['today_done'],
                                **search_params
                            },
                            priority="normal",
                        )
                        db.add(task_record)
                        db.flush()

                        async_result = yandex_search_click_task.apply_async(
                            args=[profile_id, target.id, keyword, task_record.id, search_params],
                            countdown=delay_seconds,
                            queue='yandex_search'
                        )

                        try:
                            task_record.celery_task_id = async_result.id
                        except Exception:
                            pass

                        try:
                            db.commit()
                        except Exception:
                            pass

                        scheduled_count += 1
                        scheduler_logger.info(
                            f"✅ Scheduled search visit for {target.domain} keyword='{keyword}' "
                            f"profile={profile_id} phase={chosen['phase']} "
                            f"(delay: {delay_seconds}s)"
                        )

                    except Exception as e:
                        scheduler_logger.error(
                            f"❌ Error scheduling task {i+1} for {target.domain}: {e}",
                            exc_info=True
                        )
                        continue

                target.last_visit_at = current_time
                try:
                    db.commit()
                except Exception:
                    pass

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
