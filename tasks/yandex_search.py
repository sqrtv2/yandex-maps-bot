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
import signal
from typing import Dict, List, Optional
from urllib.parse import urlparse, quote_plus
from datetime import datetime, timedelta

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from core.playwright_driver import (
    By, Keys, EC, expected_conditions,
    PlaywrightActionChains as ActionChains,
    PlaywrightWait as WebDriverWait,
    TimeoutException, NoSuchElementException, WebDriverException,
    ElementClickInterceptedException, StaleElementReferenceException,
)

from app.database import get_db_session
from app.models import BrowserProfile, Task
from app.models.yandex_search_target import YandexSearchTarget
from app.models.profile_search_visit import ProfileSearchVisit
from app.models.search_position_history import SearchPositionHistory
from core import BrowserManager, ProxyManager, CaptchaSolver
from core.browser_manager import _kill_process_tree
from core.capsola_solver import create_capsola_solver
from app.config import settings
from .celery_app import BaseTask
from celery.utils.log import get_task_logger

logger = logging.getLogger(__name__)


# Memory budget: max RSS (in MB) before we gracefully abort.
# Set below --max-memory-per-child (8GB=~7600MB) to bail BEFORE Celery SIGKILL's us.
MEMORY_BUDGET_MB = 6000  # 6 GB — leaves ~1.6 GB headroom

# Wall-clock budget: max seconds for the entire search task.
# Must be LESS than soft_time_limit (780s) to exit cleanly before Celery sends
# SoftTimeLimitExceeded (which can't interrupt Playwright FFI calls).
# 600s = 10 min: allows captcha solving to complete; leaves 3 min buffer for cleanup.
SEARCH_MAX_DURATION = 600

# Heartbeat-based watchdog: kills Chrome/node-driver when the task is STUCK
# (no heartbeat for WATCHDOG_IDLE_TIMEOUT seconds). This lets captcha solving
# take as long as needed (heartbeats keep coming) while still catching hangs.
# Absolute safety net at WATCHDOG_ABSOLUTE_MAX seconds (< time_limit).
WATCHDOG_IDLE_TIMEOUT = 120   # kill if no heartbeat for 2 minutes
WATCHDOG_ABSOLUTE_MAX = 780   # absolute max before kill (< time_limit=840s)


class _WatchdogTimeout(Exception):
    """Raised by SIGUSR1 handler when watchdog kills processes."""
    pass


def _watchdog_signal_handler(signum, frame):
    """SIGUSR1 handler: raises exception to unblock main thread after watchdog kill."""
    raise _WatchdogTimeout("Watchdog: task timeout, processes killed")


class _TaskWatchdog:
    """Background thread that force-kills Chrome/node-driver when the task is STUCK.

    Problem: When Chrome dies, the Playwright node-driver process may hang,
    blocking the Python worker on pipe.read(). soft_time_limit (Python signal)
    cannot interrupt C-level blocking calls. Celery then sends SIGKILL at
    time_limit, which kills the worker without any cleanup.

    Solution: Heartbeat-based watchdog. The task calls heartbeat() at every
    meaningful action (navigation, captcha step, API call). The watchdog thread
    checks every 5s: if no heartbeat for WATCHDOG_IDLE_TIMEOUT seconds, the task
    is stuck → kill Chrome/node-driver. This allows captcha solving to take as
    long as needed (heartbeats keep coming) while catching true hangs quickly.
    Absolute safety net at WATCHDOG_ABSOLUTE_MAX seconds.
    """

    def __init__(self):
        self._thread = None
        self._cancelled = False
        self._fired = False
        self._chrome_pid = None
        self._profile_dir = None
        self._start_time = None
        self._main_thread_id = None
        self._last_heartbeat = None  # time.time() of last heartbeat
        self._last_heartbeat_label = "init"
        import threading
        self._lock = threading.Lock()

    @property
    def fired(self):
        """True if watchdog has killed processes (skip browser.close)."""
        return self._fired

    def heartbeat(self, label: str = ""):
        """Signal that the task is still making progress. Call from main thread."""
        with self._lock:
            self._last_heartbeat = time.time()
            if label:
                self._last_heartbeat_label = label

    def start(self, start_time: float, profile_dir: str = None):
        """Start the watchdog. Call after browser is created."""
        import threading
        self._start_time = start_time
        self._last_heartbeat = time.time()
        self._profile_dir = profile_dir
        self._cancelled = False
        self._main_thread_id = threading.current_thread().ident
        self._thread = threading.Thread(target=self._run, daemon=True, name='task-watchdog')
        self._thread.start()

    def cancel(self):
        """Cancel the watchdog (call in finally block after successful cleanup)."""
        self._cancelled = True

    def _run(self):
        """Watchdog loop: check heartbeat every 5s, kill if idle too long or absolute max reached."""
        while not self._cancelled:
            time.sleep(5)
            if self._cancelled:
                return

            now = time.time()
            elapsed = now - self._start_time
            with self._lock:
                idle_time = now - self._last_heartbeat
                last_label = self._last_heartbeat_label

            # Check absolute maximum first
            if elapsed >= WATCHDOG_ABSOLUTE_MAX:
                if self._cancelled:
                    return
                self._fired = True
                logger.warning(f"⏰ WATCHDOG: absolute max {WATCHDOG_ABSOLUTE_MAX}s reached "
                               f"(elapsed={elapsed:.0f}s, last_heartbeat={last_label!r} {idle_time:.0f}s ago), "
                               f"killing Chrome/node-driver for {self._profile_dir}")
                self._force_kill()
                self._reinject_exceptions()
                return

            # Check idle timeout (no heartbeat)
            if idle_time >= WATCHDOG_IDLE_TIMEOUT:
                if self._cancelled:
                    return
                self._fired = True
                logger.warning(f"⏰ WATCHDOG: no heartbeat for {idle_time:.0f}s (limit {WATCHDOG_IDLE_TIMEOUT}s), "
                               f"last_heartbeat={last_label!r}, elapsed={elapsed:.0f}s, "
                               f"killing Chrome/node-driver for {self._profile_dir}")
                self._force_kill()
                self._reinject_exceptions()
                return

        # Cancelled normally
        return

    def _reinject_exceptions(self):
        """Keep re-injecting exceptions every 3s until task finishes or hard limit."""
        max_reinject = 10  # ~30s of retries
        for i in range(max_reinject):
            if self._cancelled:
                return
            time.sleep(3)
            if self._cancelled:
                return
            try:
                os.kill(os.getpid(), signal.SIGUSR1)
            except Exception:
                pass
            if self._main_thread_id:
                try:
                    import ctypes
                    logger.warning(f"⏰ WATCHDOG: re-injecting _WatchdogTimeout (attempt {i+2})")
                    ctypes.pythonapi.PyThreadState_SetAsyncExc(
                        ctypes.c_ulong(self._main_thread_id),
                        ctypes.py_object(_WatchdogTimeout)
                    )
                except Exception:
                    pass

    def _force_kill(self):
        """Kill all Chrome AND node-driver processes associated with our profile."""
        try:
            import psutil
            profile_dir = self._profile_dir
            if not profile_dir:
                return
            abs_dir = os.path.abspath(profile_dir)
            killed = 0

            # Strategy: find node-driver FIRST (before killing Chrome),
            # then kill node-driver tree (which kills Chrome too).
            # This ensures pipe.read() is unblocked.
            node_driver_pids = []
            chrome_pids = []

            # 1) Scan all processes: find Chrome with our profile, and their parent node-drivers
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    cmdline = ' '.join(proc.info.get('cmdline') or [])
                    name = (proc.info.get('name') or '').lower()
                    if ('chrome' in name or 'chromium' in name) and abs_dir in cmdline:
                        chrome_pids.append(proc.info['pid'])
                        # Find parent node-driver
                        try:
                            parent = psutil.Process(proc.info['pid']).parent()
                            if parent:
                                parent_cmd = ' '.join(parent.cmdline())
                                if 'run-driver' in parent_cmd:
                                    node_driver_pids.append(parent.pid)
                                    logger.warning(f"⏰ WATCHDOG: found node-driver PID={parent.pid} (parent of chrome PID={proc.info['pid']})")
                                else:
                                    # Go one more level up
                                    grandparent = parent.parent()
                                    if grandparent:
                                        gp_cmd = ' '.join(grandparent.cmdline())
                                        if 'run-driver' in gp_cmd:
                                            node_driver_pids.append(grandparent.pid)
                                            logger.warning(f"⏰ WATCHDOG: found node-driver PID={grandparent.pid} (grandparent of chrome PID={proc.info['pid']})")
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            logger.warning(f"⏰ WATCHDOG: found {len(chrome_pids)} chrome PIDs, {len(set(node_driver_pids))} node-driver PIDs for {os.path.basename(abs_dir)}")

            # 2) Kill node-drivers first (this kills Chrome children too and unblocks pipe.read())
            for pid in set(node_driver_pids):
                try:
                    logger.warning(f"⏰ WATCHDOG: killing node-driver tree PID={pid}")
                    _kill_process_tree(pid)
                    killed += 1
                except Exception as e:
                    logger.error(f"⏰ WATCHDOG: failed to kill node-driver PID={pid}: {e}")

            # 3) Kill any remaining Chrome processes not covered by node-driver tree kill
            for pid in chrome_pids:
                try:
                    if psutil.pid_exists(pid):
                        logger.warning(f"⏰ WATCHDOG: killing remaining chrome PID={pid}")
                        _kill_process_tree(pid)
                        killed += 1
                except Exception:
                    pass

            # 4) Last resort: if no node-driver found, find ALL run-driver processes
            #    and kill those that no longer have Chrome children (orphaned)
            if not node_driver_pids:
                logger.warning(f"⏰ WATCHDOG: no node-driver found via parent traversal, scanning all run-driver processes")
                for proc in psutil.process_iter(['pid', 'cmdline']):
                    try:
                        cmdline = ' '.join(proc.info.get('cmdline') or [])
                        if 'run-driver' in cmdline:
                            # Check if this node-driver's worker PID matches ours
                            try:
                                nd_parent = psutil.Process(proc.info['pid']).parent()
                                if nd_parent and nd_parent.pid == os.getpid():
                                    logger.warning(f"⏰ WATCHDOG: killing our node-driver PID={proc.info['pid']} (parent is our worker PID={os.getpid()})")
                                    _kill_process_tree(proc.info['pid'])
                                    killed += 1
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                pass
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue

            if killed:
                logger.warning(f"⏰ WATCHDOG: killed {killed} processes for {os.path.basename(abs_dir)}")
            else:
                logger.warning(f"⏰ WATCHDOG: no processes found for {os.path.basename(abs_dir)}")

            # After killing processes, unblock the main thread.
            # Playwright's sync API blocks on threading.Event.wait() which
            # doesn't notice pipe closure from killed node-driver.
            # Strategy: 1) Try SIGUSR1 signal 2) Try async exception injection
            time.sleep(2)  # Give pipe.read() a moment to unblock naturally

            # Method 1: SIGUSR1 — works if main thread is in a signal-interruptible syscall
            logger.warning(f"⏰ WATCHDOG: sending SIGUSR1 to self (PID={os.getpid()}) to unblock main thread")
            os.kill(os.getpid(), signal.SIGUSR1)

            time.sleep(1)

            # Method 2: Inject async exception into main thread via ctypes
            # This sets an exception to be raised at the next Python bytecode check
            if self._main_thread_id:
                try:
                    import ctypes
                    logger.warning(f"⏰ WATCHDOG: injecting _WatchdogTimeout into main thread (id={self._main_thread_id})")
                    ret = ctypes.pythonapi.PyThreadState_SetAsyncExc(
                        ctypes.c_ulong(self._main_thread_id),
                        ctypes.py_object(_WatchdogTimeout)
                    )
                    if ret == 0:
                        logger.warning("⏰ WATCHDOG: thread id not found for async exception")
                    elif ret > 1:
                        # Something went wrong, clear it
                        ctypes.pythonapi.PyThreadState_SetAsyncExc(
                            ctypes.c_ulong(self._main_thread_id), None
                        )
                        logger.warning("⏰ WATCHDOG: multiple threads affected, cleared")
                except Exception as ae:
                    logger.error(f"⏰ WATCHDOG: async exception injection failed: {ae}")

        except Exception as e:
            logger.error(f"⏰ WATCHDOG: error during force kill: {e}")


def _check_wall_clock(start_time: float, label: str = "", watchdog: '_TaskWatchdog' = None) -> bool:
    """Check if wall-clock budget is exceeded.
    Returns True if still within budget, raises Exception if over.
    Also sends heartbeat to watchdog if provided."""
    if watchdog:
        watchdog.heartbeat(label)
    elapsed = time.time() - start_time
    if elapsed > SEARCH_MAX_DURATION:
        raise Exception(
            f"Wall-clock budget exceeded ({elapsed:.0f}s > {SEARCH_MAX_DURATION}s)"
            + (f" at {label}" if label else "")
        )
    return True


def _check_memory_budget() -> tuple:
    """Check current process RSS against memory budget.
    Returns (over_budget: bool, rss_mb: float)."""
    try:
        import resource
        # ru_maxrss is in KB on Linux, bytes on macOS
        import platform
        rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if platform.system() == 'Darwin':
            rss_mb = rss_kb / (1024 * 1024)  # bytes → MB
        else:
            rss_mb = rss_kb / 1024  # KB → MB
        return rss_mb > MEMORY_BUDGET_MB, rss_mb
    except Exception:
        return False, 0


def _classify_error(error_str: str) -> str:
    """Classify error string into a category."""
    e = error_str.lower()
    if 'captcha' in e:
        return 'captcha'
    if 'renderer dead' in e or 'empty title' in e:
        return 'renderer_death'
    if 'click failed' in e or 'не удалось кликнуть' in e:
        return 'click_failed'
    if 'not found in search' in e or 'не найден' in e:
        return 'not_found'
    if 'softttimelimit' in e or 'timed out' in e or 'timeout' in e:
        return 'timeout'
    if 'browser window not found' in e or 'invalid session' in e or 'browser crashed' in e:
        return 'browser_crash'
    if 'err_tunnel' in e or 'err_proxy' in e or 'proxy' in e:
        return 'proxy_error'
    if 'watchdog' in e or 'worker restarted' in e or 'auto-cleanup' in e:
        return 'worker_killed'
    return 'unknown'


def _retire_profile_after_search(profile_id: int, target_id: int = None):
    """Retire profile only after it has clicked ALL active search targets (1 click per target)."""
    try:
        with get_db_session() as db:
            # Count active targets
            active_target_count = db.query(YandexSearchTarget).filter(
                YandexSearchTarget.is_active == True
            ).count()

            # Count how many distinct targets this profile has successfully clicked
            clicked_target_count = db.query(ProfileSearchVisit.search_target_id).filter(
                ProfileSearchVisit.profile_id == profile_id,
                ProfileSearchVisit.status == 'completed',
            ).distinct().count()

            if clicked_target_count >= active_target_count:
                profile = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
                if profile:
                    profile.is_active = False
                    profile.status = 'retired'
                    db.commit()
                    logger.info(
                        f"🗑️ Profile {profile_id} retired — clicked all {active_target_count} targets"
                    )
            else:
                logger.info(
                    f"♻️ Profile {profile_id} reusable — clicked {clicked_target_count}/{active_target_count} targets"
                )
    except Exception as e:
        logger.warning(f"Failed to check/retire profile {profile_id}: {e}")


def _save_error_log(task_id: int = None, task_type: str = 'yandex_search',
                    profile_id: int = None, profile_name: str = None,
                    error_message: str = '', error_detail: str = None,
                    keyword: str = None, domain: str = None,
                    proxy_host: str = None, proxy_id: int = None,
                    task_duration: int = None, error_category: str = None):
    """Save a structured error record for later analysis."""
    try:
        from app.models.error_log import ErrorLog
        if not error_category:
            error_category = _classify_error(error_message or '')
        with get_db_session() as db:
            entry = ErrorLog(
                task_id=task_id,
                task_type=task_type,
                profile_id=profile_id,
                profile_name=profile_name or (f'Profile-{profile_id}' if profile_id else None),
                error_category=error_category,
                error_message=(error_message or '')[:500],
                error_detail=(error_detail or '')[:5000] if error_detail else None,
                keyword=keyword,
                domain=domain,
                proxy_host=proxy_host,
                proxy_id=proxy_id,
                task_duration_seconds=task_duration,
            )
            db.add(entry)
            db.commit()
    except Exception as e:
        logger.warning(f"Failed to save error log: {e}")


def _update_search_task_log(task_id: int, message: str, status: str = None,
                            error: str = None, result_data: dict = None, exec_time: float = None):
    """Update search task record in DB."""
    try:
        with get_db_session() as db:
            task_obj = db.query(Task).filter(Task.id == task_id).first()
            if task_obj:
                # Don't resurrect tasks that were already terminated by cleanup/watchdog
                if status == 'in_progress' and task_obj.status in ('failed', 'completed', 'not_found', 'cancelled'):
                    logger.warning(f"Task {task_id} already {task_obj.status}, skipping in_progress update")
                    return
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
                if status in ('completed', 'failed', 'not_found'):
                    task_obj.completed_at = datetime.utcnow()
                db.commit()
    except Exception as e:
        logger.warning(f"Failed to update search task log: {e}")


def _safe_click(driver, element, pause_min=0.3, pause_max=0.8):
    """Safely click an element using Playwright's native click (trusted events).
    
    Uses element.click() which does proper actionability checks, scrolling,
    and dispatches trusted browser events — same approach that fixed typing.
    """
    # Get raw Playwright ElementHandle for direct operations
    from playwright.sync_api import ElementHandle as _EH
    raw = None
    if hasattr(element, '_handle'):
        raw = element._handle
    elif isinstance(element, _EH):
        raw = element

    # Strategy 1: Playwright ElementHandle.click() — trusted events with actionability checks
    try:
        if raw:
            raw.scroll_into_view_if_needed(timeout=3000)
            time.sleep(random.uniform(pause_min, pause_max))
            raw.click(timeout=5000)
            return
        elif hasattr(element, 'click'):
            element.click()
            return
    except Exception as e1:
        logger.info(f"_safe_click: Playwright click failed ({e1}), trying mouse click...")

    # Strategy 2: Mouse click at element coordinates (more control)
    try:
        if raw:
            raw.evaluate("el => el.scrollIntoView({behavior: 'smooth', block: 'center'})")
        else:
            driver.execute_script(
                "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});",
                element
            )
        time.sleep(random.uniform(0.3, 0.5))
        ActionChains(driver).move_to_element(element).pause(
            random.uniform(pause_min, pause_max)
        ).click().perform()
        return
    except Exception as e2:
        logger.info(f"_safe_click: ActionChains click failed ({e2}), trying JS click...")

    # Strategy 3: JS click fallback
    try:
        if raw:
            raw.evaluate("el => el.click()")
        else:
            driver.execute_script("arguments[0].click();", element)
    except Exception as js_err:
        logger.warning(f"_safe_click: all click strategies failed (JS: {js_err})")
        raise


def _dismiss_yandex_overlays(driver):
    """Dismiss Yandex 'download app' overlay (DistributionSplashScreen) and similar modals
    that intercept pointer events and block pagination clicks."""
    try:
        dismissed = driver.execute_script("""
            var dominated = false;
            // 1. DistributionSplashScreen close button
            var closeSelectors = [
                '.DistributionSplashScreenModalScene .modal__close',
                '.DistributionSplashScreenModalScene [class*="close"]',
                '.DistributionSplashScreenModalScene button',
                '[class*="DistributionSplash"] [class*="close"]',
                '[class*="DistributionSplash"] [class*="Close"]',
                '[class*="DistributionSplash"] button[class*="close" i]',
                '[class*="DistributionSplash"] button',
                '.Modal-Content [class*="close"]',
            ];
            for (var i = 0; i < closeSelectors.length; i++) {
                var btn = document.querySelector(closeSelectors[i]);
                if (btn && btn.offsetParent !== null) {
                    btn.click();
                    dominated = true;
                    break;
                }
            }
            // 2. Remove the overlay entirely if close button didn't work
            if (!dominated) {
                var overlays = document.querySelectorAll(
                    '.DistributionSplashScreenModalScene, ' +
                    '[id*="DistributionSplashscreen"], ' +
                    '[class*="DistributionSplash"]'
                );
                for (var j = 0; j < overlays.length; j++) {
                    overlays[j].remove();
                    dominated = true;
                }
            }
            return dominated;
        """)
        if dismissed:
            logger.info("  🗑️ Dismissed Yandex distribution overlay")
            time.sleep(random.uniform(0.5, 1.0))
        return dismissed
    except Exception as e:
        logger.debug(f"  _dismiss_yandex_overlays: {e}")
        return False


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
        _safe_get(driver, f"https://ya.ru/search/?text={encoded}", timeout=40, label="search direct")
        time.sleep(random.uniform(4, 7))
        
        final_url = driver.current_url.lower()
        final_url_path = final_url.split('?')[0]
        if 'showcaptcha' in final_url_path or 'checkcaptcha' in final_url_path:
            logger.warning(f"❌ Still on captcha after direct navigation: {final_url[:100]}")
            return False
        logger.info(f"✅ Navigated to search results: {driver.current_url[:120]}")
    return True


def _human_scroll(driver, min_scrolls=2, max_scrolls=5):
    """Simulate human-like scrolling using mouse wheel events with variable speed.
    
    Uses page.mouse.wheel() to emit real wheel events instead of JS scrollBy,
    with acceleration/deceleration curves to mimic trackpad/mouse behavior.
    """
    num_scrolls = random.randint(min_scrolls, max_scrolls)
    page = getattr(driver, '_page', None)
    
    for i in range(num_scrolls):
        # Total scroll distance for this "gesture"
        total_delta = random.randint(200, 600)
        
        if page and hasattr(page, 'mouse'):
            # Smooth wheel scroll: break into micro-steps with acceleration curve
            num_steps = random.randint(4, 10)
            # Generate bell-curve distribution for step sizes (accelerate then decelerate)
            raw_weights = [math.sin(math.pi * (j + 0.5) / num_steps) for j in range(num_steps)]
            weight_sum = sum(raw_weights)
            for j in range(num_steps):
                step_delta = int(total_delta * raw_weights[j] / weight_sum)
                if step_delta < 1:
                    step_delta = 1
                try:
                    page.mouse.wheel(0, step_delta)
                except Exception:
                    # Fallback to JS if wheel fails
                    driver.execute_script(f"window.scrollBy(0, {step_delta})")
                # Variable micro-delay between wheel ticks (30-120ms, faster in middle)
                micro_delay = random.uniform(0.03, 0.12)
                time.sleep(micro_delay)
        else:
            # Fallback: JS scrollBy for non-Playwright drivers
            driver.execute_script(f"window.scrollBy(0, {total_delta})")
        
        # Pause between scroll gestures — sometimes short (reading), sometimes long (browsing)
        if random.random() < 0.3:
            time.sleep(random.uniform(1.5, 4.0))  # Long pause — "reading"
        else:
            time.sleep(random.uniform(0.4, 1.5))  # Short pause
    
    # Sometimes scroll back up a bit (30% chance)
    if random.random() < 0.3:
        up_amount = random.randint(80, 250)
        if page and hasattr(page, 'mouse'):
            up_steps = random.randint(3, 6)
            for j in range(up_steps):
                step = int(up_amount / up_steps)
                try:
                    page.mouse.wheel(0, -step)
                except Exception:
                    driver.execute_script(f"window.scrollBy(0, -{step})")
                time.sleep(random.uniform(0.03, 0.08))
        else:
            driver.execute_script(f"window.scrollBy(0, -{up_amount})")
        time.sleep(random.uniform(0.5, 1.0))


def _human_mouse_move(driver, duration=None):
    """Simulate idle mouse movement between actions.
    
    Moves the mouse cursor to random positions on the page with natural
    Bezier-like curves. Real users constantly move their mouse while reading.
    """
    page = getattr(driver, '_page', None)
    if not page or not hasattr(page, 'mouse'):
        return
    
    if duration is None:
        duration = random.uniform(0.5, 2.0)
    
    try:
        # Get viewport dimensions
        vp = page.viewport_size or {'width': 1366, 'height': 768}
        vw, vh = vp['width'], vp['height']
        
        # Number of movement points
        num_moves = random.randint(2, 5)
        start_time = time.time()
        
        for _ in range(num_moves):
            if time.time() - start_time > duration:
                break
            # Target: random position biased towards content area
            target_x = random.randint(int(vw * 0.1), int(vw * 0.85))
            target_y = random.randint(int(vh * 0.15), int(vh * 0.8))
            
            # Move with steps to simulate smooth curve
            steps = random.randint(3, 8)
            try:
                page.mouse.move(target_x, target_y, steps=steps)
            except Exception:
                break
            time.sleep(random.uniform(0.1, 0.5))
    except Exception:
        pass  # Non-critical, never fail on mouse movement


def _safe_get(driver, url, timeout=40, label="page"):
    """Navigate to URL with timeout handling.
    Returns True if loaded, False if timed out, 'dead' if browser/page is dead.
    
    Includes a backup threading.Timer that calls Page.stopLoading via CDP
    if Playwright's internal timeout doesn't fire (e.g. dead proxy).
    """
    try:
        driver.set_page_load_timeout(timeout)
        driver.get(url)
        return True
    except TimeoutException:
        logger.warning(f"⏱️ {label} timed out after {timeout}s, stopping page load: {url[:100]}")
        # Use CDP Page.stopLoading to abort pending navigation.
        # DO NOT use window.stop() via JS — after goto() timeout, the navigation
        # is still pending internally and evaluate_handle() waits for it to settle
        # BEFORE starting its own timeout, causing infinite hang with dead proxy.
        try:
            driver.execute_cdp_cmd("Page.stopLoading")
        except Exception:
            pass
        return False
    except Exception as e:
        err_str = str(e).lower()
        if 'closed' in err_str or 'target' in err_str:
            logger.warning(f"💀 {label} navigation error — browser/page DEAD: {e}")
            return 'dead'
        logger.warning(f"⚠️ {label} navigation error: {e}")
        return False


# === Shared analytics blocking configuration ===

# URL patterns to block at network level (Playwright route uses Python regex)
_ANALYTICS_ROUTE_PATTERNS = re.compile(
    r'(mc\.yandex\.|metrika|metrica|webvisor|informer\.yandex|'
    r'google-analytics\.com|googletagmanager\.com|analytics\.google\.com|'
    r'top-fwz1\.mail\.ru|top\.mail\.ru|counter\.yadro\.ru|'
    r'hotjar\.com|mouseflow\.com|clarity\.ms|'
    r'connect\.facebook\.net|bat\.bing\.com|'
    r'an\.yandex\.ru|yandexadexchange|ads\.adfox\.ru|'
    r'rating\.openstat|pixel\.wp\.com|'
    r'jivosite\.com|calltouch|callibri|envybox\.io|comagic\.ru|'
    r'livetex\.ru|talk-me\.ru|chatra\.io|carrotquest\.io|'
    r'pagead2\.googlesyndication|adservice\.google|doubleclick\.net)',
    re.IGNORECASE
)

# Flag to track if route blocking is already set up on the context
_route_blocking_set_up = set()  # set of context ids


def _setup_playwright_route_blocking(driver):
    """Set up Playwright context.route() to block analytics requests at network level.
    
    This is the RELIABLE way to block requests in modern Chrome (145+).
    CDP Network.setBlockedURLs was deprecated in Chrome 99 and does nothing.
    
    context.route() intercepts requests BEFORE they are sent and aborts them.
    Works across all pages/tabs in the context, survives navigation.
    Only set up once per context.
    """
    try:
        context = driver._context
        ctx_id = id(context)
        if ctx_id in _route_blocking_set_up:
            return  # Already set up
        
        def _block_analytics(route):
            try:
                route.abort()
            except Exception:
                pass
        
        # Use regex pattern matching — Playwright route() accepts regex
        context.route(_ANALYTICS_ROUTE_PATTERNS, _block_analytics)
        _route_blocking_set_up.add(ctx_id)
        logger.info("🛡️ Playwright route blocking set up (context-level, all analytics patterns)")
    except Exception as e:
        logger.warning(f"⚠️ Failed to set up Playwright route blocking: {e}")
_ANALYTICS_BLOCKED_URLS = [
    # Yandex Metrika — all known endpoints
    '*mc.yandex.ru*',
    '*mc.yandex.com*',
    '*metrika.yandex.ru*',
    '*metrica.yandex.com*',
    '*cdn.metrika.yandex.net*',
    '*watch.metrika*',
    '*informer.yandex.ru*',
    '*webvisor*',
    '*webvisor2*',
    # Metrika script files
    '*metrika/tag*',
    '*metrika/watch*',
    # Google Analytics / Tag Manager
    '*google-analytics.com*',
    '*googletagmanager.com*',
    '*gtag*',
    '*analytics.google.com*',
    # Other trackers
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
    '*an.yandex.ru*',
    '*yandexadexchange*',
    '*ads.adfox.ru*',
    # --- Heavy resources: fonts ---
    '*fonts.googleapis.com*',
    '*fonts.gstatic.com*',
    '*.woff2*',
    '*.woff*',
    '*.ttf*',
    '*.otf*',
    # --- Heavy resources: video/media ---
    '*.mp4*',
    '*.webm*',
    '*.m3u8*',
    '*video.yandex*',
    '*strm.yandex.*',
    '*player.vimeo.com*',
    '*youtube.com/embed*',
    '*youtube.com/iframe*',
    '*rutube.ru/play/embed*',
    # --- Ads & social widgets ---
    '*pagead2.googlesyndication.com*',
    '*adservice.google.*',
    '*doubleclick.net*',
    '*ad.mail.ru*',
    '*ssp.rambler.ru*',
    '*vk.com/js/api/openapi*',
    '*vk.com/share*',
    '*platform.twitter.com*',
    '*cdn.jsdelivr.net/npm/jquery*',
    # --- Chat widgets ---
    '*jivosite.com*',
    '*calltouch.*',
    '*callibri.*',
    '*envybox.io*',
    '*comagic.ru*',
    '*livetex.ru*',
    '*talk-me.ru*',
    '*chatra.io*',
    '*carrotquest.io*',
]

# Comprehensive JS that kills all analytics BEFORE any page script runs.
# Used via Page.addScriptToEvaluateOnNewDocument (runs before ANY page JS).
_ANALYTICS_KILL_JS = """
(function() {
    // === ANALYTICS KILLER — runs before any page scripts ===

    // 1. Kill sendBeacon immediately (Metrika uses it to send data on unload)
    if (navigator.sendBeacon) {
        navigator.sendBeacon = function() { return true; };
        Object.defineProperty(navigator, 'sendBeacon', {
            value: function() { return true; },
            writable: false, configurable: false
        });
    }

    // 2. Neuter Yandex Metrika objects
    window.Ya = window.Ya || {};
    window.Ya.Metrika2 = function() {
        return {
            reachGoal: function(){}, hit: function(){}, params: function(){},
            getClientID: function(){ return '0'; }, setUserID: function(){},
            userParams: function(){}, clickmap: function(){}, trackLinks: function(){},
            accurateTrackBounce: function(){}, extLink: function(){},
            file: function(){}, notBounce: function(){}, firstPartyParams: function(){}
        };
    };
    window.Ya.Metrika = window.Ya.Metrika2;
    Object.defineProperty(window, 'ym', {
        value: function() {},
        writable: false, configurable: false
    });

    // 3. Neuter Google Analytics
    Object.defineProperty(window, 'ga', {
        value: function() {},
        writable: false, configurable: false
    });
    Object.defineProperty(window, 'gtag', {
        value: function() {},
        writable: false, configurable: false
    });
    Object.defineProperty(window, 'dataLayer', {
        value: [],
        writable: false, configurable: false
    });

    // 4. Block fetch() to analytics domains
    var _origFetch = window.fetch;
    window.fetch = function(url) {
        var urlStr = (typeof url === 'string') ? url : (url && url.url ? url.url : '');
        if (urlStr && (
            urlStr.indexOf('mc.yandex') !== -1 || urlStr.indexOf('metrika') !== -1 ||
            urlStr.indexOf('metrica') !== -1 || urlStr.indexOf('google-analytics') !== -1 ||
            urlStr.indexOf('googletagmanager') !== -1 || urlStr.indexOf('webvisor') !== -1 ||
            urlStr.indexOf('hotjar') !== -1 || urlStr.indexOf('clarity.ms') !== -1 ||
            urlStr.indexOf('top-fwz1.mail.ru') !== -1 || urlStr.indexOf('an.yandex') !== -1
        )) {
            return Promise.resolve(new Response('', {status: 200}));
        }
        return _origFetch.apply(this, arguments);
    };

    // 5. Block XMLHttpRequest to analytics domains
    var _origXHROpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function(method, url) {
        if (typeof url === 'string' && (
            url.indexOf('mc.yandex') !== -1 || url.indexOf('metrika') !== -1 ||
            url.indexOf('metrica') !== -1 || url.indexOf('google-analytics') !== -1 ||
            url.indexOf('googletagmanager') !== -1 || url.indexOf('webvisor') !== -1
        )) {
            this._analyticsBlocked = true;
            return;
        }
        return _origXHROpen.apply(this, arguments);
    };
    var _origXHRSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function() {
        if (this._analyticsBlocked) return;
        return _origXHRSend.apply(this, arguments);
    };

    // 6. Block Image beacon (Metrika uses new Image().src = 'mc.yandex.ru/...')
    var _OrigImage = window.Image;
    window.Image = function(w, h) {
        var img = new _OrigImage(w, h);
        var _srcDesc = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'src');
        Object.defineProperty(img, 'src', {
            set: function(val) {
                if (val && (val.indexOf('mc.yandex') !== -1 || val.indexOf('metrika') !== -1 ||
                    val.indexOf('metrica') !== -1 || val.indexOf('google-analytics') !== -1 ||
                    val.indexOf('an.yandex') !== -1 || val.indexOf('informer.yandex') !== -1)) {
                    return; // Block
                }
                if (_srcDesc && _srcDesc.set) _srcDesc.set.call(img, val);
            },
            get: function() {
                if (_srcDesc && _srcDesc.get) return _srcDesc.get.call(img);
            }
        });
        return img;
    };
    window.Image.prototype = _OrigImage.prototype;

    // 7. Block script element creation for analytics
    var _origCreateElement = document.createElement;
    document.createElement = function(tag) {
        var el = _origCreateElement.call(document, tag);
        if (tag.toLowerCase() === 'script') {
            var _srcDescS = Object.getOwnPropertyDescriptor(HTMLScriptElement.prototype, 'src');
            Object.defineProperty(el, 'src', {
                set: function(val) {
                    if (val && (val.indexOf('metrika') !== -1 || val.indexOf('mc.yandex') !== -1 ||
                        val.indexOf('metrica') !== -1 || val.indexOf('google-analytics') !== -1 ||
                        val.indexOf('googletagmanager') !== -1 || val.indexOf('webvisor') !== -1 ||
                        val.indexOf('hotjar') !== -1 ||
                        val.indexOf('clarity.ms') !== -1 || val.indexOf('an.yandex') !== -1)) {
                        return; // Block analytics script
                    }
                    if (_srcDescS && _srcDescS.set) _srcDescS.set.call(el, val);
                },
                get: function() {
                    if (_srcDescS && _srcDescS.get) return _srcDescS.get.call(el);
                }
            });
        }
        return el;
    };

    // 8. MutationObserver — remove any <script> or <img> with analytics src that bypass above
    try {
        var obs = new MutationObserver(function(mutations) {
            for (var i = 0; i < mutations.length; i++) {
                var nodes = mutations[i].addedNodes;
                for (var j = 0; j < nodes.length; j++) {
                    var node = nodes[j];
                    if (node.nodeType !== 1) continue;
                    var src = node.src || node.getAttribute && node.getAttribute('src') || '';
                    if (src && (src.indexOf('mc.yandex') !== -1 || src.indexOf('metrika') !== -1 ||
                        src.indexOf('metrica') !== -1 || src.indexOf('google-analytics') !== -1 ||
                        src.indexOf('googletagmanager') !== -1 || src.indexOf('webvisor') !== -1 ||
                        src.indexOf('tag.js') !== -1 || src.indexOf('an.yandex') !== -1)) {
                        node.remove();
                    }
                }
            }
        });
        obs.observe(document.documentElement || document, {childList: true, subtree: true});
    } catch(e) {}

    // 9. Block iframe creation for analytics
    var _origCreateEl2 = document.createElement;
    // Already overridden above, extend for iframes
    var _prevCreate = document.createElement;
    document.createElement = function(tag) {
        var el = _prevCreate.call(document, tag);
        if (tag.toLowerCase() === 'iframe') {
            var _srcDescI = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'src');
            Object.defineProperty(el, 'src', {
                set: function(val) {
                    if (val && (val.indexOf('mc.yandex') !== -1 || val.indexOf('metrika') !== -1 ||
                        val.indexOf('metrica') !== -1 || val.indexOf('informer') !== -1)) {
                        return;
                    }
                    if (_srcDescI && _srcDescI.set) _srcDescI.set.call(el, val);
                },
                get: function() {
                    if (_srcDescI && _srcDescI.get) return _srcDescI.get.call(el);
                }
            });
        }
        return el;
    };
})();
"""


def _pre_inject_analytics_blocker(driver):
    """Pre-inject analytics blocker before ANY page navigation to target site.
    
    Three layers of defense:
    1. Playwright context.route() — blocks requests at network level (most reliable)
    2. CDP Page.addScriptToEvaluateOnNewDocument — kills analytics JS objects before page scripts
    3. Short page load timeout — allows fast abort via Page.stopLoading
    
    MUST be called BEFORE the click that navigates to the target site.
    """
    # Layer 1: Playwright route blocking (reliable, works in Chrome 145+)
    _setup_playwright_route_blocking(driver)
    
    try:
        if hasattr(driver, 'execute_cdp_cmd'):
            # Layer 2: Pre-inject JS killer that runs before ANY page scripts
            result = driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                'source': _ANALYTICS_KILL_JS
            })
            logger.info(f"🛡️ Pre-injected analytics killer via CDP (id={result.get('identifier', '?')})")

            # Layer 3: Set short page load timeout so we can abort fast
            try:
                driver.set_page_load_timeout(5)
            except Exception:
                pass
        else:
            logger.warning("⚠️ CDP not available for pre-injection")
    except Exception as e:
        logger.warning(f"⚠️ Failed to pre-inject analytics blocker: {e}")


def _block_analytics_on_target(driver):
    """Block analytics on the target site — route blocking + JS neutralization."""
    # Playwright route blocking should already be active, but ensure it
    _setup_playwright_route_blocking(driver)
    
    # Also inject JS to neuter analytics objects on the current page
    try:
        driver.execute_script(_ANALYTICS_KILL_JS)
        logger.info("🛡️ Injected analytics neutralization JS")
    except Exception as e:
        logger.warning(f"⚠️ Failed to inject analytics neutralization JS: {e}")


def _abort_page_load_fast(driver, wait_before_abort=None):
    """Abort page loading quickly to prevent analytics scripts from executing.
    
    Uses CDP Page.stopLoading (immediate, doesn't depend on JS context)
    followed by window.stop() as fallback.
    """
    if wait_before_abort is None:
        wait_before_abort = random.uniform(0.1, 0.3)
    
    time.sleep(wait_before_abort)
    
    # CDP Page.stopLoading — immediate, works even if JS context is blocked
    try:
        driver.execute_cdp_cmd("Page.stopLoading")
        logger.info(f"🛑 Page load aborted via CDP after {wait_before_abort:.1f}s")
    except Exception:
        # Fallback to JS
        try:
            driver.execute_script("window.stop();")
            logger.info(f"🛑 Page load aborted via JS after {wait_before_abort:.1f}s")
        except Exception as e:
            logger.warning(f"⚠️ Page abort failed: {e}")
    
    # Re-inject analytics neutralization after stop
    try:
        driver.execute_script(_ANALYTICS_KILL_JS)
    except Exception:
        pass


def _human_read_page(driver, min_time=5, max_time=15):
    """Simulate reading a page: scroll, pause, and move mouse."""
    read_time = random.uniform(min_time, max_time)
    start = time.time()
    while time.time() - start < read_time:
        _human_scroll(driver, 1, 2)
        # Occasionally move mouse between scrolls (simulates looking at content)
        if random.random() < 0.5:
            _human_mouse_move(driver, duration=random.uniform(0.3, 1.0))
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
                           serp_position: int = None, referrer_used: bool = False):
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
                referrer_used=referrer_used,
                checked_at=datetime.utcnow()
            )
            db.add(record)
            db.commit()
            logger.debug(f"📊 Position history saved: {keyword} → {domain} "
                        f"{'p' + str(page) + '#' + str(position) if found else 'NOT_FOUND'}")
    except Exception as e:
        logger.error(f"Failed to save position history: {e}")


def _check_and_disable_keyword(target_id: int, keyword: str, domain: str, consecutive_threshold: int = 3):
    """
    Check if the last N searches for this keyword+target all resulted in not_found.
    If so, auto-disable the keyword in the target.
    """
    with get_db_session() as db:
        # Get last N position history records for this keyword+target, newest first
        recent = db.query(SearchPositionHistory).filter(
            SearchPositionHistory.search_target_id == target_id,
            SearchPositionHistory.keyword == keyword,
        ).order_by(SearchPositionHistory.checked_at.desc()).limit(consecutive_threshold).all()

        if len(recent) < consecutive_threshold:
            return  # Not enough data yet

        # Check if all recent records are not_found
        all_not_found = all(not r.found for r in recent)
        if not all_not_found:
            return  # At least one was found, don't disable

        # Auto-disable keyword
        target = db.query(YandexSearchTarget).filter(YandexSearchTarget.id == target_id).first()
        if not target:
            return

        disabled = target.get_disabled_keywords_set()
        if keyword.strip().lower() in disabled:
            return  # Already disabled

        target.disable_keyword(keyword)
        db.commit()
        logger.warning(
            f"🚫 Auto-disabled keyword '{keyword}' for {domain} "
            f"(not found {consecutive_threshold} consecutive times)"
        )


def _calculate_keyword_clicks(db, target_id: int, keyword: str, target_success_rate: float = 100.0, freq_weight: float = 1.0) -> dict:
    """
    Gradual position-adaptive click calculation targeting TOP-3.
    
    Philosophy:
    - Goal is TOP-3.  Once there → maintain with 2-3 clicks/day.
    - Outside TOP-3 → gradually increase clicks.  If no growth (stagnation)
      → escalate further, up to 24/day (1/hour) or 48/day (1/30min) for
      long stagnation.
    - Recovery after drop from TOP-3 → start at 4-6, build up if no progress.
    - Normal limit: 24 clicks/day (1 per hour).
    - Stagnation 10+ days: up to 48 clicks/day (1 per 30 min).
    - freq_weight: multiplier (0.7-1.5) based on exact Wordstat frequency.
    
    Returns dict with clicks_per_day, phase, current_position, trend, reason.
    """
    scheduler_logger = logging.getLogger(__name__ + '.strategy')
    
    try:
        # ── Gather history ──
        since_14d = datetime.utcnow() - timedelta(days=14)
        since_7d  = datetime.utcnow() - timedelta(days=7)
        since_3d  = datetime.utcnow() - timedelta(days=3)
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        
        records_14d = db.query(SearchPositionHistory).filter(
            SearchPositionHistory.search_target_id == target_id,
            SearchPositionHistory.keyword == keyword,
            SearchPositionHistory.checked_at >= since_14d
        ).order_by(SearchPositionHistory.checked_at.asc()).all()
        
        records_7d = [r for r in records_14d if r.checked_at >= since_7d]
        records_3d = [r for r in records_14d if r.checked_at >= since_3d]
        
        # Count ALL attempts today (not just clicks) to prevent
        # infinite retry of keywords that are never found in search results
        today_clicks = db.query(SearchPositionHistory).filter(
            SearchPositionHistory.search_target_id == target_id,
            SearchPositionHistory.keyword == keyword,
            SearchPositionHistory.checked_at >= today_start
        ).count()
        
        # ── No data → start with 6 clicks ──
        if not records_14d:
            clicks = max(2, int(math.ceil(6 * freq_weight)))
            return {
                "clicks_per_day": clicks,
                "today_done": today_clicks,
                "phase": "start",
                "current_position": None,
                "trend": "unknown",
                "reason": f"Новый ключ — старт {clicks} кликов/день"
            }
        
        # ── Calculate positions ──
        found_14d = [r for r in records_14d if r.found and r.absolute_position]
        found_7d  = [r for r in records_7d  if r.found and r.absolute_position]
        found_3d  = [r for r in records_3d  if r.found and r.absolute_position]
        
        if not found_14d:
            clicks = max(2, int(math.ceil(6 * freq_weight)))
            return {
                "clicks_per_day": clicks,
                "today_done": today_clicks,
                "phase": "not_found",
                "current_position": None,
                "trend": "not_found",
                "reason": f"Не найден в выдаче — {clicks} попыток/день"
            }
        
        # Current position = average of last 3 days
        recent_positions = ([r.absolute_position for r in found_3d] 
                           if found_3d else [r.absolute_position for r in found_7d[-3:]])
        if not recent_positions:
            # Fallback: use all found_14d positions
            recent_positions = [r.absolute_position for r in found_14d]
        current_pos = sum(recent_positions) / len(recent_positions) if recent_positions else 50.0
        
        # Previous position = average of days 4-7
        earlier_positions = [r.absolute_position for r in found_7d if r.checked_at < since_3d]
        prev_pos = sum(earlier_positions) / len(earlier_positions) if earlier_positions else current_pos
        
        # Week-before position = average of days 8-14  
        week_before = [r.absolute_position for r in found_14d if r.checked_at < since_7d]
        week_before_pos = sum(week_before) / len(week_before) if week_before else None
        
        # ── Trend calculation ──
        if len(found_7d) < 3:
            trend = "unknown"
        else:
            diff = prev_pos - current_pos  # positive = improving
            if diff > 2:
                trend = "improving"
            elif diff < -2:
                trend = "declining"
            else:
                trend = "stable"
        
        # ── Stagnation detection ──
        # How many days position hasn't meaningfully changed?
        days_stagnant = 0
        if week_before_pos is not None and abs(week_before_pos - current_pos) < 3:
            days_stagnant = 10  # ~2 weeks of no movement
        elif week_before_pos is not None and abs(week_before_pos - current_pos) < 5:
            days_stagnant = 7
        elif trend == "stable":
            days_stagnant = 3
        
        # ── Was consistently in TOP-3 during last 14 days? ──
        # Require 3+ measurements AND 30%+ of found results in TOP-3
        # to avoid false triggers from single lucky outliers
        top3_count_14d = sum(1 for r in found_14d if r.absolute_position <= 3)
        top3_count_7d = sum(1 for r in found_7d if r.absolute_position <= 3)
        top3_ratio_14d = top3_count_14d / len(found_14d) if found_14d else 0
        top3_ratio_7d = top3_count_7d / len(found_7d) if found_7d else 0
        was_top3 = top3_count_14d >= 3 and top3_ratio_14d >= 0.3
        was_top3_recently = top3_count_7d >= 3 and top3_ratio_7d >= 0.3
        best_14d = min(r.absolute_position for r in found_14d)
        
        # ── CORE ALGORITHM: Gradual clicks targeting TOP-3 ──
        # Normal cap: 24/day (1/hour).  Stagnation 10+ days: up to 48/day (1/30min).
        
        if current_pos <= 3:
            # ✅ TOP-3 — maintenance
            if days_stagnant >= 7:
                # Stable in TOP-3 for a long time → minimal support
                clicks = 1
                phase = "maintain"
                reason = f"TOP-3 стабильно (поз. {current_pos:.1f}, {days_stagnant}д) — 1 клик/день"
            else:
                clicks = 2
                phase = "maintain"
                reason = f"TOP-3 (поз. {current_pos:.1f}) — поддержка 2 клика/день"
            if trend == "declining":
                clicks = 5
                phase = "maintain"
                reason = f"TOP-3 но падает ({current_pos:.1f}) — усиление до 5"
        
        elif current_pos <= 5:
            # TOP-5 — push toward TOP-3
            clicks = 6
            phase = "ramp_up"
            reason = f"TOP-5 ({current_pos:.1f}) — продвижение в TOP-3: 6 кликов"
            if trend == "improving":
                clicks = 8
                reason = f"TOP-5 и растёт ({current_pos:.1f}) — ускорение 8 кликов"
            elif trend == "declining":
                clicks = 10
                reason = f"TOP-5 но падает ({current_pos:.1f}) — усиление 10 кликов"
            elif days_stagnant >= 7:
                clicks = 12
                reason = f"TOP-5 застой ({current_pos:.1f}, {days_stagnant}д) — 12 кликов"
        
        elif current_pos <= 10:
            # TOP-10 — active push
            clicks = 10
            phase = "ramp_up"
            reason = f"TOP-10 ({current_pos:.1f}) — активное продвижение 10 кликов"
            if trend == "improving":
                clicks = 12
                reason = f"TOP-10 и растёт ({current_pos:.1f}) — 12 кликов"
            elif days_stagnant >= 7:
                clicks = 16
                reason = f"TOP-10 застой ({current_pos:.1f}, {days_stagnant}д) — усиление 16"
            elif days_stagnant >= 10:
                clicks = 20
                reason = f"TOP-10 долгий застой ({current_pos:.1f}, {days_stagnant}д) — 20 кликов"
            if was_top3 and trend == "declining":
                clicks = 6
                phase = "recovery"
                reason = f"Выпал из TOP-3 → {current_pos:.1f} — восстановление 6 кликов"
        
        elif current_pos <= 20:
            # Page 2 — aggressive promotion
            clicks = 10
            phase = "ramp_up"
            reason = f"Стр.2 ({current_pos:.1f}) — 10 кликов"
            if trend == "improving":
                clicks = 14
                reason = f"Стр.2 и растёт ({current_pos:.1f}) — 14 кликов"
            elif days_stagnant >= 7:
                clicks = 18
                reason = f"Стр.2 застой ({current_pos:.1f}, {days_stagnant}д) — 18 кликов"
            elif days_stagnant >= 10:
                clicks = 24
                reason = f"Стр.2 долгий застой ({current_pos:.1f}, {days_stagnant}д) — 24 кл/день"
            if was_top3 and not was_top3_recently:
                clicks = 6
                phase = "recovery"
                reason = f"Был TOP-3, упал → стр.2 ({current_pos:.1f}) — восстановление 6 кликов"
            elif was_top3_recently:
                clicks = 4
                phase = "recovery"
                reason = f"Недавно выпал из TOP-3 → {current_pos:.1f} — старт восстановления 4 клика"
        
        elif current_pos <= 30:
            # Page 3 — building up
            clicks = 8
            phase = "ramp_up"
            reason = f"Стр.3 ({current_pos:.1f}) — 8 кликов"
            if trend == "improving":
                clicks = 12
                reason = f"Стр.3 и растёт ({current_pos:.1f}) — 12 кликов"
            elif days_stagnant >= 7:
                clicks = 16
                reason = f"Стр.3 застой ({current_pos:.1f}, {days_stagnant}д) — 16 кликов"
            elif days_stagnant >= 10:
                clicks = 24
                reason = f"Стр.3 долгий застой ({current_pos:.1f}, {days_stagnant}д) — 24 кл/день"
            if was_top3:
                clicks = 4
                phase = "recovery"
                reason = f"Был TOP-3, упал → стр.3 ({current_pos:.1f}) — плавный старт 4 клика"
        
        elif current_pos <= 50:
            # Pages 4-5 — far from goal
            clicks = 6
            phase = "start"
            reason = f"Далеко ({current_pos:.1f}) — 6 кликов"
            if trend == "improving":
                clicks = 10
                phase = "ramp_up"
                reason = f"Далеко но растёт ({current_pos:.1f}) — 10 кликов"
            elif days_stagnant >= 7:
                clicks = 14
                phase = "ramp_up"
                reason = f"Далеко, застой ({current_pos:.1f}, {days_stagnant}д) — 14 кликов"
            elif days_stagnant >= 10:
                clicks = 20
                phase = "ramp_up"
                reason = f"Далеко, долгий застой ({current_pos:.1f}, {days_stagnant}д) — 20 кликов"
        
        else:
            # Very far (50+) — exploration
            clicks = 4
            phase = "start"
            reason = f"Очень далеко ({current_pos:.1f}) — 4 клика"
            if days_stagnant >= 10:
                clicks = 8
                reason = f"Очень далеко, застой ({current_pos:.1f}, {days_stagnant}д) — 8 кликов"
        
        # ── Frequency weight (subtle: 0.7 – 1.5) ──
        if freq_weight != 1.0 and freq_weight > 0:
            clicks = max(1, int(math.ceil(clicks * freq_weight)))
        
        # ── Success rate correction ──
        effective_clicks = clicks
        if target_success_rate > 0 and target_success_rate < 95:
            rate = max(target_success_rate, 10.0) / 100.0
            clicks = int(math.ceil(effective_clicks / rate))
            reason += f" (корр. {target_success_rate:.0f}%)"
        
        # No artificial daily cap — position strategy is the sole authority.
        # visits_per_day on the target is a scale-up goal, not a ceiling.
        
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
                var isAd = item.querySelector('.label_theme_direct, .DirectBanner, [class*="Direct"], [class*="Ad_type"], [class*="Ad_label"], [class*="AdvLabel"]');
                if (isAd) continue;
                
                // Skip ads by text markers (Яндекс Директ labels)
                var textContent = item.textContent || '';
                if (textContent.indexOf('Реклама') !== -1 && textContent.indexOf('Реклама') < 200) continue;
                
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
                
                // Skip Yandex Direct ad clicks (yabs.yandex.ru = always ads)
                if (href.indexOf('yabs.yandex.ru') !== -1 || href.indexOf('an.yandex.ru') !== -1) continue;
                
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
                
                // Skip Yandex internal widgets (images, maps, popular products, etc.)
                var domLower = domain.toLowerCase();
                if (domLower === 'ya.ru' || domLower === 'yandex.ru') {
                    // Allow actual yandex.ru organic results (sub-services like realty.yandex.ru)
                    // but skip search/images/maps redirects
                    if (href.indexOf('/search') !== -1 || href.indexOf('/images') !== -1 || 
                        href.indexOf('/maps') !== -1 || href.indexOf('/video') !== -1) {
                        continue;
                    }
                }
                
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
            
            // Strategy 3 fallback: generic approach — find all visible links with external hrefs
            if (results.length < 3) {
                var allLinks = document.querySelectorAll('a[href]');
                var seenHrefs = {};
                for (var m = 0; m < results.length; m++) seenHrefs[results[m].href] = true;
                
                for (var n = 0; n < allLinks.length; n++) {
                    var a = allLinks[n];
                    var ah = a.href || '';
                    if (!ah || ah === '#' || seenHrefs[ah]) continue;
                    // Must be external or /clck redirect
                    if (ah.indexOf('yandex.ru/clck') === -1 && ah.indexOf('ya.ru/clck') === -1) {
                        try {
                            var au = new URL(ah);
                            if (au.hostname.indexOf('yandex') !== -1 || au.hostname.indexOf('ya.ru') !== -1) continue;
                        } catch(e) { continue; }
                    }
                    // Skip tiny/invisible elements
                    var ar = a.getBoundingClientRect();
                    if (ar.height < 12 || ar.width < 40) continue;
                    // Skip ads
                    if (ah.indexOf('yabs.yandex.ru') !== -1 || ah.indexOf('an.yandex.ru') !== -1) continue;
                    
                    var ad = '';
                    try {
                        if (ah.indexOf('/clck') !== -1) {
                            // Try parent's text for green URL
                            var parentItem = a.closest('[data-cid], .serp-item, [class*="Organic"]');
                            var greenEl3 = parentItem ? parentItem.querySelector('[class*="Path"], [class*="greenurl"], [data-log-node="path"]') : null;
                            ad = greenEl3 ? greenEl3.textContent.trim().replace(/^https?:\\/\\//, '').replace(/^www\\./, '').split('/')[0].split(' ')[0] : '';
                        } else {
                            ad = new URL(ah).hostname.replace(/^www\\./, '');
                        }
                    } catch(e) { continue; }
                    if (!ad) continue;
                    
                    seenHrefs[ah] = true;
                    results.push({
                        index: results.length,
                        title: a.textContent.trim().substring(0, 100),
                        href: ah,
                        domain: ad.toLowerCase(),
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


def _find_and_click_target(driver, domain: str, max_pages: int = 3, keyword: str = None,
                           task_start_time: float = None) -> dict:
    """
    Search through Yandex search results to find and click target domain.
    Uses JS-based extraction for reliable result parsing.
    
    Returns:
        dict with keys: found (bool), page (int), position (int), clicked (bool)
    """
    from tasks.yandex_maps import detect_captcha_or_block, handle_yandex_protection
    
    domain_clean = domain.lower().replace('https://', '').replace('http://', '').replace('www.', '').rstrip('/')
    
    # Time budget: leave 120s buffer before soft_time_limit (540s) for click-through browsing
    TIME_BUDGET = 420  # seconds max for search phase (7 min)
    _start = task_start_time or time.time()
    
    prev_page_domains = set()  # Track previous page domains for dedup detection
    
    for page_num in range(1, max_pages + 1):
        # === Time budget check: bail out if running too long ===
        elapsed = time.time() - _start
        if page_num > 1 and elapsed > TIME_BUDGET:
            logger.warning(f"⏰ Time budget exceeded ({elapsed:.0f}s > {TIME_BUDGET}s) before page {page_num}, stopping search")
            break
        
        # === Memory budget check: bail out gracefully before Celery SIGKILL's us ===
        if page_num > 1:
            over_budget, rss_mb = _check_memory_budget()
            if over_budget:
                logger.warning(f"🧠 Memory budget exceeded ({rss_mb:.0f}MB > {MEMORY_BUDGET_MB}MB) before page {page_num}, stopping search to avoid SIGKILL")
                break
        
        logger.info(f"🔍 Scanning search results page {page_num} for domain: {domain_clean} (elapsed: {elapsed:.0f}s)")
        
        # Log actual page from URL for monitoring (p=0 is page 1, p=1 is page 2, etc.)
        try:
            from urllib.parse import urlparse as _up_log, parse_qs as _pq_log
            _log_parsed = _pq_log(_up_log(driver.current_url).query)
            _url_page = int(_log_parsed.get('p', ['0'])[0]) + 1
            if _url_page != page_num:
                logger.warning(f"  ⚠️ URL says page {_url_page} but we expect page {page_num}! URL: {driver.current_url[:150]}")
        except Exception:
            pass
        
        time.sleep(random.uniform(2, 4))
        
        # === Detect Chrome network errors (proxy/DNS failure) ===
        current_url_check = driver.current_url.lower()
        if 'chrome-error://' in current_url_check:
            # Extract error type from page text if possible
            _err_text = ''
            try:
                _err_text = driver.find_element(By.TAG_NAME, 'body').text[:300]
            except Exception:
                pass
            _err_msg = f"Chrome network error on page {page_num}: {_err_text[:200]}" if _err_text else f"Chrome network error on page {page_num}"
            logger.error(f"  🚫 {_err_msg} (URL={driver.current_url[:120]})")
            raise Exception(_err_msg)
        
        # === Safety check: if we're on ya.ru homepage (not search results), navigate to search ===
        current_url_path = current_url_check.split('?')[0]
        if page_num == 1 and keyword and '/search' not in current_url_check and 'text=' not in current_url_check \
                and 'showcaptcha' not in current_url_path and 'checkcaptcha' not in current_url_path:
            logger.warning(f"  ⚠️ Not on search page (URL={driver.current_url[:120]}), navigating to search directly...")
            encoded = quote_plus(keyword)
            _safe_get(driver, f"https://ya.ru/search/?text={encoded}", timeout=40, label="search redirect")
            time.sleep(random.uniform(4, 7))
            # Re-check for chrome-error after redirect attempt
            if 'chrome-error://' in driver.current_url.lower():
                _err_text = ''
                try:
                    _err_text = driver.find_element(By.TAG_NAME, 'body').text[:300]
                except Exception:
                    pass
                raise Exception(f"Chrome network error after redirect: {_err_text[:200]}")
        
        # Save current URL to verify pagination later
        url_before_scan = driver.current_url
        
        # === Wait for search results to render ===
        try:
            WebDriverWait(driver, 15).until(
                lambda d: len(d.find_elements(By.CSS_SELECTOR, '[data-cid]')) >= 3
            )
        except TimeoutException:
            logger.warning(f"  ⏱️ Waited 15s but [data-cid] elements < 3 on page {page_num}")
        
        # === Extract organic results via JavaScript ===
        organic_results = _extract_organic_results_js(driver)
        
        # If 0 results, save diagnostic info and retry once after extra wait
        if not organic_results:
            diag_url = driver.current_url
            diag_title = driver.title
            diag_cid_count = len(driver.find_elements(By.CSS_SELECTOR, '[data-cid]'))
            diag_serp_count = len(driver.find_elements(By.CSS_SELECTOR, '.serp-item, li.serp-item'))
            logger.warning(
                f"  ⚠️ 0 organic results on page {page_num}! "
                f"URL={diag_url[:150]}, Title='{diag_title}', "
                f"data-cid={diag_cid_count}, serp-item={diag_serp_count}"
            )
            # Save page source + screenshot for debugging
            try:
                ts = int(time.time())
                html_path = f"screenshots/search_0results_p{page_num}_{ts}.html"
                page_src = driver.page_source or ''
                with open(html_path, 'w', encoding='utf-8') as f:
                    f.write(page_src)
                logger.info(f"  📄 Zero-results page source: {html_path} ({len(page_src)} bytes)")
                # Save screenshot
                try:
                    scr_path = f"screenshots/search_0results_p{page_num}_{ts}.png"
                    driver.save_screenshot(scr_path)
                    logger.info(f"  📸 Zero-results screenshot: {scr_path}")
                except Exception:
                    pass
            except Exception:
                pass
            
            # Check if it's a captcha page — also detect by title
            # (sometimes URL hasn't changed to /showcaptcha yet but the page is a captcha)
            _captcha_titles = ('верификация', 'вы не робот', 'подтвердите', 'captcha', 'robot')
            _diag_title_lower = (diag_title or '').lower()
            _is_captcha_by_title = any(ct in _diag_title_lower for ct in _captcha_titles)
            if _is_captcha_by_title:
                logger.warning(f"  🚨 Captcha detected by page title: '{diag_title}'")
            url_path = diag_url.lower().split('?')[0]
            if _is_captcha_by_title or 'showcaptcha' in url_path or 'checkcaptcha' in url_path or detect_captcha_or_block(driver):
                logger.warning(f"  🚨 Page {page_num} is a captcha page, attempting to solve...")
                # === Solve captcha during pagination ===
                try:
                    captcha_solver = CaptchaSolver()
                    solved_pag = handle_yandex_protection(driver, captcha_solver, max_kaleidoscope_attempts=3)
                    if solved_pag:
                        logger.info(f"  ✅ Captcha on page {page_num} solved, retrying extraction...")
                        # After solving, check if we're back on search or need to re-navigate
                        time.sleep(random.uniform(2, 4))
                        post_url = driver.current_url.lower()
                        if 'search' in post_url and 'text=' in post_url:
                            # We're on search results — wait for render and retry
                            try:
                                WebDriverWait(driver, 15).until(
                                    lambda d: len(d.find_elements(By.CSS_SELECTOR, '[data-cid]')) >= 3
                                )
                            except TimeoutException:
                                pass
                            organic_results = _extract_organic_results_js(driver)
                            logger.info(f"  🔄 After captcha solve: found {len(organic_results)} organic results")
                        else:
                            logger.warning(f"  ⚠️ After captcha solve, not on search page: {post_url[:120]}")
                    else:
                        logger.warning(f"  ❌ Could not solve captcha on page {page_num}")
                except SoftTimeLimitExceeded:
                    raise
                except Exception as cap_err:
                    logger.warning(f"  ❌ Captcha solve error on page {page_num}: {cap_err}")
            elif diag_cid_count > 0:
                # DOM has data-cid elements but JS extraction failed — wait more and retry
                logger.info(f"  🔄 {diag_cid_count} data-cid elements exist, waiting 5s and retrying extraction...")
                time.sleep(5)
                organic_results = _extract_organic_results_js(driver)
                logger.info(f"  🔄 Retry: found {len(organic_results)} organic results")
            else:
                # data-cid=0, no captcha — page JS may not have rendered yet (SPA)
                logger.info(f"  🔄 No data-cid elements and no captcha, waiting 10s for JS render...")
                time.sleep(10)
                # Check if page rendered now
                post_cid = len(driver.find_elements(By.CSS_SELECTOR, '[data-cid]'))
                if post_cid > 0:
                    organic_results = _extract_organic_results_js(driver)
                    logger.info(f"  🔄 After extra wait: found {len(organic_results)} organic results (data-cid={post_cid})")
                else:
                    # Try page reload up to 2 times as last resort
                    for _reload_num in range(1, 3):
                        logger.info(f"  🔄 Still no results, refreshing page (attempt {_reload_num})...")
                        try:
                            # Stop any pending loads to avoid renderer timeout on refresh
                            try:
                                driver.execute_script("window.stop()")
                            except Exception:
                                pass
                            time.sleep(0.5)
                            driver.refresh()
                            time.sleep(random.uniform(3, 5))
                            try:
                                WebDriverWait(driver, 15).until(
                                    lambda d: len(d.find_elements(By.CSS_SELECTOR, '[data-cid]')) >= 3
                                )
                            except TimeoutException:
                                pass
                            # Check for captcha after refresh
                            if detect_captcha_or_block(driver):
                                logger.warning(f"  🚨 Captcha appeared after refresh on page {page_num}")
                                try:
                                    captcha_solver = CaptchaSolver()
                                    solved_ref = handle_yandex_protection(driver, captcha_solver, max_kaleidoscope_attempts=3)
                                    if solved_ref:
                                        time.sleep(random.uniform(2, 4))
                                except SoftTimeLimitExceeded:
                                    raise
                                except Exception:
                                    pass
                            organic_results = _extract_organic_results_js(driver)
                            logger.info(f"  🔄 After refresh #{_reload_num}: found {len(organic_results)} organic results")
                            if organic_results:
                                break
                        except Exception as ref_err:
                            logger.warning(f"  ⚠️ Page refresh #{_reload_num} failed: {ref_err}")
                            break
                    
                    # If still 0 results after refreshes, try fresh navigation to search URL
                    if not organic_results and keyword and page_num == 1:
                        logger.warning(f"  🔄 Refreshes didn't help, navigating to search URL directly...")
                        encoded = quote_plus(keyword)
                        _safe_get(driver, f"https://ya.ru/search/?text={encoded}", timeout=40, label="search recovery")
                        time.sleep(random.uniform(4, 7))
                        try:
                            WebDriverWait(driver, 15).until(
                                lambda d: len(d.find_elements(By.CSS_SELECTOR, '[data-cid]')) >= 3
                            )
                        except TimeoutException:
                            pass
                        if detect_captcha_or_block(driver):
                            try:
                                captcha_solver = CaptchaSolver()
                                solved_nav = handle_yandex_protection(driver, captcha_solver, max_kaleidoscope_attempts=3)
                                if solved_nav:
                                    time.sleep(random.uniform(2, 4))
                            except SoftTimeLimitExceeded:
                                raise
                            except Exception:
                                pass
                        organic_results = _extract_organic_results_js(driver)
                        logger.info(f"  🔄 After direct navigation: found {len(organic_results)} organic results")
        
        logger.info(f"  Found {len(organic_results)} organic results on page {page_num}")
        
        # Log first 10 results for debugging
        for res in organic_results[:10]:
            logger.info(f"    #{res['index']+1}: {res['title'][:50]} → {res['domain']} ({res['href'][:80]})")
        
        # === Dedup detection: if this page's results match previous page, pagination failed ===
        current_page_domains = set(r['domain'] for r in organic_results if r.get('domain'))
        if page_num > 1 and prev_page_domains and current_page_domains:
            overlap = current_page_domains & prev_page_domains
            overlap_ratio = len(overlap) / max(len(current_page_domains), 1)
            if overlap_ratio > 0.6:
                logger.warning(f"  🔄 Page {page_num} results are {overlap_ratio:.0%} identical to page {page_num - 1} — pagination failed!")
                logger.warning(f"  🔄 Forcing URL navigation to get real page {page_num}...")
                # Force URL navigation as a full page reload
                try:
                    from urllib.parse import urlparse as _up2, parse_qs as _pq2, urlencode as _ue2
                    parsed_url = _up2(driver.current_url)
                    url_params = _pq2(parsed_url.query)
                    url_params['p'] = [str(page_num - 1)]  # p=0 is page 1
                    new_q = _ue2({k: v[0] for k, v in url_params.items()})
                    reload_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}?{new_q}"
                    logger.info(f"  ➡️ Reloading page {page_num} via URL: {reload_url[:120]}")
                    _safe_get(driver, reload_url, timeout=40, label=f"page {page_num} dedup reload")
                    time.sleep(random.uniform(3, 5))
                    # Wait for results
                    try:
                        WebDriverWait(driver, 15).until(
                            lambda d: len(d.find_elements(By.CSS_SELECTOR, '[data-cid]')) >= 3
                        )
                    except TimeoutException:
                        pass
                    # Check captcha after reload
                    reload_url_check = driver.current_url.lower()
                    reload_url_path = reload_url_check.split('?')[0]
                    if 'showcaptcha' in reload_url_path or 'checkcaptcha' in reload_url_path or detect_captcha_or_block(driver):
                        logger.warning(f"  🚨 Captcha after dedup reload on page {page_num}, solving...")
                        try:
                            pag_solver2 = CaptchaSolver()
                            pag_solved2 = handle_yandex_protection(driver, pag_solver2, max_kaleidoscope_attempts=3)
                            if pag_solved2:
                                logger.info(f"  ✅ Dedup reload captcha solved")
                                time.sleep(random.uniform(2, 4))
                        except SoftTimeLimitExceeded:
                            raise
                        except Exception:
                            pass
                    # Re-extract results  
                    organic_results = _extract_organic_results_js(driver)
                    current_page_domains = set(r['domain'] for r in organic_results if r.get('domain'))
                    logger.info(f"  🔄 After dedup reload: found {len(organic_results)} organic results on page {page_num}")
                    for res in organic_results[:10]:
                        logger.info(f"    #{res['index']+1}: {res['title'][:50]} → {res['domain']} ({res['href'][:80]})")
                except Exception as dedup_err:
                    logger.warning(f"  ❌ Dedup reload failed: {dedup_err}")
        prev_page_domains = current_page_domains
        
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
            
            # Skip ad results (yabs.yandex.ru, an.yandex.ru are always ads)
            res_href = res.get('href', '').lower()
            if 'yabs.yandex.ru' in res_href or 'an.yandex.ru' in res_href or 'ads-captcha.yandex.ru' in res_href:
                logger.info(f"    ⛔ Skipping ad result #{position}: {res['title'][:50]} → {res_href[:80]}")
                continue
            
            if domain_clean in res_domain or domain_clean in green_domain or \
               res_domain.endswith('.' + domain_clean) or domain_clean in res_href:
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
                logger.warning(f"   ⚠️ Found target in DOM data but cannot get clickable element, trying direct navigation")
                # Even if we can't find the element, try navigating directly
                direct_href = target_result.get('href', '')
                if direct_href and direct_href.startswith('http'):
                    try:
                        _pre_inject_analytics_blocker(driver)
                        logger.info(f"   🔄 Direct navigation to: {direct_href[:100]}")
                        driver.get(direct_href)
                        # Abort immediately — route blocking handles analytics
                        _abort_page_load_fast(driver, wait_before_abort=random.uniform(0.3, 0.6))
                        try:
                            final_url = driver.current_url.lower()
                            final_host = urlparse(final_url).netloc.lower().replace('www.', '')
                            if domain_clean in final_host or domain_clean in final_url:
                                logger.info(f"   ✅ Direct navigation succeeded: {final_host}")
                                return {
                                    'found': True,
                                    'page': page_num,
                                    'position': target_position,
                                    'clicked': True,
                                    'href': target_result['href'],
                                    'serp_position': target_position
                                }
                        except Exception:
                            pass
                    except Exception as nav_err:
                        logger.warning(f"   Direct navigation failed: {nav_err}")
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
            
            # Dismiss overlays (app banners etc.) that may block the click
            _dismiss_yandex_overlays(driver)
            
            # Remember windows before click
            windows_before = driver.window_handles
            
            # === PRE-INJECT ANALYTICS BLOCKER BEFORE CLICK ===
            # Critical: Page.addScriptToEvaluateOnNewDocument persists across all
            # navigations and new tabs. The injected JS runs BEFORE any page scripts,
            # so Metrica is dead on arrival when the target page loads.
            # Network.setBlockedURLs blocks requests at network level (never sent).
            _pre_inject_analytics_blocker(driver)
            
            # Click the result — Yandex /clck redirect registers the click
            # Pre-extract href for fallback navigation
            target_href = ''
            try:
                target_href = click_element.get_attribute('href') or target_result.get('href', '')
            except Exception:
                target_href = target_result.get('href', '')
            
            # Capture Yandex /clck/ tracking URL by dispatching mousedown first
            # (Yandex JS changes <a> href on mousedown to include click tracking)
            clck_href = ''
            try:
                clck_href = driver.execute_script("""
                    var el = arguments[0];
                    var rect = el.getBoundingClientRect();
                    var cx = rect.left + rect.width / 2;
                    var cy = rect.top + rect.height / 2;
                    var opts = {bubbles: true, cancelable: true, view: window, clientX: cx, clientY: cy, button: 0};
                    el.dispatchEvent(new MouseEvent('mousedown', opts));
                    // Only mousedown — Yandex JS rewrites href on mousedown.
                    // Do NOT dispatch mouseup to avoid triggering a navigation/new tab.
                    return el.href || el.getAttribute('href') || '';
                """, click_element)
                if clck_href and '/clck/' in clck_href:
                    logger.info(f"   🔗 Captured Yandex tracking URL: {clck_href[:120]}")
                elif clck_href:
                    logger.info(f"   🔗 Href after mousedown (no /clck/): {clck_href[:120]}")
            except Exception as me_err:
                logger.info(f"   ⚠️ Could not capture /clck/ URL: {me_err}")
            
            click_succeeded = False
            try:
                _safe_click(driver, click_element)
                click_succeeded = True
            except Exception as click_err:
                logger.warning(f"   Click failed: {click_err}")
                # Fallback: navigate via /clck/ tracking URL or direct href
                try:
                    fallback_href = clck_href or target_href
                    if fallback_href and fallback_href.startswith('http'):
                        logger.info(f"   🔄 Fallback: navigating to ({fallback_href[:120]})")
                        driver.get(fallback_href)
                        click_succeeded = True
                    else:
                        # Try JS click on the parent <a> tag
                        driver.execute_script("""
                            var el = arguments[0];
                            var link = el.closest('a') || el.querySelector('a');
                            if (link) link.click();
                            else el.click();
                        """, click_element)
                        click_succeeded = True
                except Exception as fallback_err:
                    logger.warning(f"   Fallback click also failed: {fallback_err}")
            
            # === Wait for Yandex click redirect ===
            # Click goes yandex.ru/clck → target site.
            # Playwright route blocking is already active (kills analytics requests).
            # We poll URL to detect when we leave Yandex, then abort immediately.
            time.sleep(random.uniform(0.5, 1.0))
            
            # Check if new tab was opened
            windows_after = driver.window_handles
            if len(windows_after) > len(windows_before):
                new_window = [w for w in windows_after if w not in windows_before][0]
                logger.info(f"   New tab opened, switching to it")
                driver.switch_to.window(new_window)
                
                # Immediately stop loading in new tab — analytics route blocking is context-wide
                try:
                    driver.execute_cdp_cmd("Page.stopLoading")
                    logger.info("   🛑 Page.stopLoading in new tab")
                except Exception:
                    try:
                        driver.execute_script("window.stop();")
                    except Exception:
                        pass
                time.sleep(random.uniform(0.1, 0.3))
            
            # Check if we left Yandex
            try:
                current_url = driver.current_url.lower()
            except TimeoutException:
                current_url = ''
                logger.info("   ⏳ current_url timed out (page loading, analytics pre-blocked)")
            except Exception:
                current_url = ''
            if current_url:
                logger.info(f"   Current URL after click: {current_url[:150]}")
            
            # If still on Yandex redirect, wait briefly
            if current_url and ('yandex.ru/clck' in current_url or 'ya.ru/clck' in current_url):
                logger.info(f"   Still on Yandex redirect, waiting...")
                time.sleep(random.uniform(1.5, 3.0))
                try:
                    current_url = driver.current_url.lower()
                except TimeoutException:
                    current_url = ''
                except Exception:
                    current_url = ''
            
            # === Smart navigation wait with aggressive abort ===
            def _is_on_yandex(url):
                host = urlparse(url).netloc.lower().replace('www.', '')
                return 'ya.ru' in host or 'yandex.ru' in host
            
            still_on_yandex = _is_on_yandex(current_url) if current_url else True
            
            if still_on_yandex:
                # Poll URL — once off Yandex, immediately abort page load.
                # Playwright route blocking handles analytics. We just need to stop
                # the page from fully loading (stops remaining resource requests).
                max_nav_checks = 5
                nav_wait_each = random.uniform(0.4, 0.7)
                used_fallback = False
                for nav_i in range(max_nav_checks):
                    time.sleep(nav_wait_each)
                    
                    # --- Check for new tabs from previous click ---
                    try:
                        windows_now = driver.window_handles
                    except Exception:
                        windows_now = windows_before
                    if len(windows_now) > len(windows_before):
                        new_window = [w for w in windows_now if w not in windows_before][-1]
                        logger.info(f"   New tab detected at check {nav_i+1}, switching to it")
                        driver.switch_to.window(new_window)
                        # Route blocking is context-wide, just stop loading
                        try:
                            driver.execute_cdp_cmd("Page.stopLoading")
                        except Exception:
                            try:
                                driver.execute_script("window.stop();")
                            except Exception:
                                pass
                        break
                    
                    try:
                        current_url = driver.current_url.lower()
                    except TimeoutException:
                        # Page is loading off Yandex — abort immediately
                        logger.info(f"   ⏳ URL timed out at poll {nav_i+1} — page loading, aborting...")
                        try:
                            driver.execute_cdp_cmd("Page.stopLoading")
                        except Exception:
                            pass
                        try:
                            current_url = driver.current_url.lower()
                        except Exception:
                            current_url = ''
                        break
                    except Exception:
                        current_url = ''
                        break
                    if not _is_on_yandex(current_url):
                        logger.info(f"   ✅ Left Yandex after {(nav_i+1)*nav_wait_each:.1f}s: {current_url[:120]}")
                        # IMMEDIATELY abort page load via CDP (fastest method)
                        try:
                            driver.execute_cdp_cmd("Page.stopLoading")
                            logger.info("   🛑 Page.stopLoading — page load aborted")
                        except Exception:
                            try:
                                driver.execute_script("window.stop();")
                            except Exception:
                                pass
                        break
                    if 'clck' in current_url:
                        logger.info(f"   ⏳ Still on /clck (check {nav_i+1}/{max_nav_checks})...")
                        continue
                    # Still on SERP — use ONE URL-based fallback (no extra click events
                    # that would open additional tabs)
                    if not used_fallback and nav_i >= 2:
                        nav_href = clck_href or target_href or target_result.get('href', '')
                        if nav_href and nav_href.startswith('http'):
                            logger.info(f"   🔄 Still on SERP, navigating to: {nav_href[:120]}")
                            used_fallback = True
                            try:
                                driver.get(nav_href)
                            except TimeoutException:
                                logger.info(f"   ⏳ driver.get() timed out, stopping page load...")
                                try:
                                    driver.execute_script("window.stop();")
                                except Exception:
                                    pass
                            except Exception as nav_err:
                                logger.warning(f"   driver.get() failed: {nav_err}")
                            break
                else:
                    logger.warning(f"   ⏰ Navigation did not happen after {max_nav_checks} checks")
            else:
                # Already on target site — block analytics & abort immediately
                _block_analytics_on_target(driver)
                _abort_page_load_fast(driver, wait_before_abort=random.uniform(0.1, 0.4))
            
            # Restore normal page_load_timeout
            try:
                driver.set_page_load_timeout(40)
            except Exception:
                pass
            
            # Final verification
            try:
                final_url = driver.current_url.lower()
                final_host = urlparse(final_url).netloc.lower().replace('www.', '')
            except Exception:
                final_url = ''
                final_host = ''
            
            on_yandex = _is_on_yandex(final_url) if final_url else True
            clicked = (not on_yandex) and (domain_clean in final_host or domain_clean in final_url)
            
            if on_yandex and not clicked:
                logger.warning(f"   Still on Yandex after click: {final_url[:100]}")
            
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
            
            # Scroll to the very bottom to trigger lazy-loading of pagination
            try:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(random.uniform(1.5, 2.5))
                # Scroll a bit more in case of dynamic content loading
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(random.uniform(1.0, 2.0))
            except Exception:
                pass
            
            # Method 1: Click next button (preferred — natural, avoids captcha)
            try:
                next_selectors = [
                    "a.pager__item_kind_next",
                    "a[aria-label='Следующая страница']",
                    "a.Pager-Item_type_next",
                    ".pager__item_kind_next",
                    ".pager-load-more a",
                    "a.pager-more__button",
                    ".pager a[aria-label*='След']",
                    "a[data-fast-name='next']",
                    ".Pager .Pager-Item_type_next a",
                    "a.VanillaReact.Pager-Item_type_next",
                    "[class*='Pager'] a[aria-label*='next' i]",
                    "[class*='Pager'] a[aria-label*='след' i]",
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
                    # Dismiss any Yandex overlay that might intercept the click
                    _dismiss_yandex_overlays(driver)
                    
                    logger.info(f"  ➡️ Found pagination button, clicking to go to page {page_num + 1}")
                    
                    # Before click: capture first result href to detect content change
                    first_href_before = None
                    try:
                        first_href_before = driver.execute_script("""
                            var el = document.querySelector('[data-cid] a.OrganicTitle-Link, [data-cid] h2 a[href]');
                            return el ? el.href : null;
                        """)
                    except Exception:
                        pass
                    
                    _safe_click(driver, next_btn, 0.3, 0.7)
                    
                    # Wait for actual content change (not just elements existing)
                    content_changed = False
                    if first_href_before:
                        try:
                            WebDriverWait(driver, 12).until(
                                lambda d: d.execute_script("""
                                    var el = document.querySelector('[data-cid] a.OrganicTitle-Link, [data-cid] h2 a[href]');
                                    return el ? el.href : null;
                                """) != first_href_before
                            )
                            content_changed = True
                            logger.info(f"  ✅ Page content changed after button click")
                        except TimeoutException:
                            logger.warning(f"  ⚠️ Content didn't change after button click — SPA may not have updated")
                    
                    if not content_changed:
                        time.sleep(random.uniform(2, 4))
                        # Extra wait and scroll to trigger any lazy rendering
                        try:
                            driver.execute_script("window.scrollTo(0, 0);")
                            time.sleep(1)
                        except Exception:
                            pass
                    
                    # Final check: wait for [data-cid] elements
                    try:
                        WebDriverWait(driver, 10).until(
                            lambda d: len(d.find_elements(By.CSS_SELECTOR, '[data-cid]')) >= 3
                        )
                    except TimeoutException:
                        logger.warning(f"  ⏱️ Search results slow to load on page {page_num + 1}")
                    
                    # Check for captcha after pagination
                    pag_url = driver.current_url.lower()
                    pag_url_path = pag_url.split('?')[0]
                    if 'showcaptcha' in pag_url_path or 'checkcaptcha' in pag_url_path or detect_captcha_or_block(driver):
                        logger.warning(f"  🚨 Captcha appeared after navigating to page {page_num + 1}, solving...")
                        try:
                            pag_solver = CaptchaSolver()
                            pag_solved = handle_yandex_protection(driver, pag_solver, max_kaleidoscope_attempts=3)
                            if pag_solved:
                                logger.info(f"  ✅ Pagination captcha solved")
                                time.sleep(random.uniform(2, 4))
                                # After captcha, retpath often redirects to page 1 (no p= param).
                                # Check if we're on the correct page, re-navigate if not.
                                post_captcha_url = driver.current_url
                                if 'search' in post_captcha_url and 'text=' in post_captcha_url and page_num > 0:
                                    from urllib.parse import urlparse as _up_cap, parse_qs as _pq_cap
                                    _pc_parsed = _up_cap(post_captcha_url)
                                    _pc_params = _pq_cap(_pc_parsed.query)
                                    _pc_page = int(_pc_params.get('p', ['0'])[0])
                                    if _pc_page != page_num:
                                        logger.warning(
                                            f"  🔄 Captcha redirected to page {_pc_page + 1} instead of {page_num + 1}, re-navigating..."
                                        )
                                        _pc_params['p'] = [str(page_num)]
                                        from urllib.parse import urlencode as _ue_cap
                                        _correct_query = _ue_cap({k: v[0] for k, v in _pc_params.items()})
                                        _correct_url = f"{_pc_parsed.scheme}://{_pc_parsed.netloc}{_pc_parsed.path}?{_correct_query}"
                                        _safe_get(driver, _correct_url, timeout=40, label=f"page {page_num + 1} (post-captcha fix)")
                                        time.sleep(random.uniform(2, 4))
                                        # Check for another captcha after re-navigation
                                        _pc2_url = driver.current_url.lower()
                                        if 'showcaptcha' in _pc2_url or 'checkcaptcha' in _pc2_url:
                                            logger.warning(f"  🚨 Another captcha after re-navigation to page {page_num + 1}")
                                            try:
                                                pag_solved2 = handle_yandex_protection(driver, pag_solver, max_kaleidoscope_attempts=3)
                                                if pag_solved2:
                                                    time.sleep(random.uniform(2, 4))
                                            except SoftTimeLimitExceeded:
                                                raise
                                            except Exception:
                                                pass
                            else:
                                logger.warning(f"  ❌ Could not solve pagination captcha")
                        except SoftTimeLimitExceeded:
                            raise
                        except Exception as pag_cap_err:
                            logger.warning(f"  ❌ Pagination captcha error: {pag_cap_err}")
                    
                    # Verify we're on a search page (not captcha) and content changed
                    post_nav_url = driver.current_url.lower()
                    on_search_page = 'search' in post_nav_url and 'text=' in post_nav_url
                    if on_search_page and content_changed:
                        next_page_success = True
                        logger.info(f"  ✅ Navigated to page {page_num + 1} via button (content confirmed changed)")
                    elif on_search_page:
                        next_page_success = True
                        logger.warning(f"  ⚠️ URL changed but content may not have updated for page {page_num + 1}")
                    else:
                        logger.warning(f"  ⚠️ Button click didn't reach search page (URL: {driver.current_url[:100]})")
                else:
                    logger.info(f"  ⚠️ No pagination button found on page")
            except Exception as nav_err:
                logger.warning(f"  Button pagination failed: {nav_err}")
            
            # Method 2: Direct URL manipulation (fallback — may trigger captcha)
            if not next_page_success:
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
                        _safe_get(driver, next_url, timeout=40, label=f"page {page_num + 1}")
                        time.sleep(random.uniform(2, 4))
                        
                        # Wait for search results to appear on new page
                        try:
                            WebDriverWait(driver, 15).until(
                                lambda d: len(d.find_elements(By.CSS_SELECTOR, '[data-cid]')) >= 3
                            )
                        except TimeoutException:
                            logger.warning(f"  ⏱️ Search results slow to load on page {page_num + 1}")
                        
                        # Check for captcha after pagination
                        pag_url = driver.current_url.lower()
                        pag_url_path = pag_url.split('?')[0]
                        if 'showcaptcha' in pag_url_path or 'checkcaptcha' in pag_url_path or detect_captcha_or_block(driver):
                            logger.warning(f"  🚨 Captcha appeared after navigating to page {page_num + 1}, solving...")
                            try:
                                pag_solver = CaptchaSolver()
                                pag_solved = handle_yandex_protection(driver, pag_solver, max_kaleidoscope_attempts=3)
                                if pag_solved:
                                    logger.info(f"  ✅ Pagination captcha solved")
                                    time.sleep(random.uniform(2, 4))
                                    # After solving, we might need to re-navigate to the page
                                    post_solve_url = driver.current_url.lower()
                                    if 'text=' not in post_solve_url or 'search' not in post_solve_url:
                                        logger.info(f"  🔄 Re-navigating to page {page_num + 1} after captcha solve...")
                                        _safe_get(driver, next_url, timeout=40, label=f"page {page_num + 1} (post-captcha)")
                                        time.sleep(random.uniform(2, 4))
                                else:
                                    logger.warning(f"  ❌ Could not solve pagination captcha")
                            except SoftTimeLimitExceeded:
                                raise
                            except Exception as pag_cap_err:
                                logger.warning(f"  ❌ Pagination captcha error: {pag_cap_err}")
                        
                        # Verify URL changed
                        new_url = driver.current_url
                        if new_url != url_before_scan:
                            next_page_success = True
                            logger.info(f"  ✅ On page {page_num + 1}")
                        else:
                            logger.warning(f"  ⚠️ URL didn't change after pagination")
                except Exception as url_nav_err:
                    logger.warning(f"  URL pagination failed: {url_nav_err}")
            
            if not next_page_success:
                logger.info(f"  ❌ Could not navigate to page {page_num + 1}, stopping")
                break
    
    return {'found': False, 'page': max_pages, 'position': 0, 'clicked': False}


@shared_task(base=BaseTask, bind=True, max_retries=1, default_retry_delay=30,
             soft_time_limit=780, time_limit=840)
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
    _watchdog = _TaskWatchdog()  # Watchdog kills Chrome before SIGKILL
    params = search_params or {}
    proxy_data = None  # Initialize here so it's always in scope for error handlers
    domain = None  # Initialize so error handler can reference it even if DB fails early

    try:
        start_time = time.time()

        # ── Mark task as in_progress IMMEDIATELY ──
        # This MUST happen before any code that can fail or retry.
        # Otherwise, failed tasks stay as 'pending' in DB even though Celery consumed
        # the message, which blocks the scheduler ("buffer_full") for 15 minutes.
        if task_id:
            _update_search_task_log(task_id, f"🚀 Задача принята воркером", status='in_progress')

        # Register SIGUSR1 handler for watchdog timeout
        # When watchdog kills Chrome/node-driver, pipe.read() may not unblock.
        # Watchdog then sends SIGUSR1 which raises _WatchdogTimeout in main thread.
        _prev_sigusr1 = signal.getsignal(signal.SIGUSR1)
        signal.signal(signal.SIGUSR1, _watchdog_signal_handler)

        # Load target config
        with get_db_session() as db:
            target = db.query(YandexSearchTarget).filter(YandexSearchTarget.id == target_id).first()
            if not target:
                raise ValueError(f"Search target {target_id} not found")
            domain = target.domain
            max_pages = params.get('max_search_pages', target.max_search_pages) or 5
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
                'platform': profile_obj.platform,
                'is_mobile': profile_obj.is_mobile,
                'canvas_fingerprint': profile_obj.canvas_fingerprint,
                'webgl_fingerprint': profile_obj.webgl_fingerprint,
                'audio_fingerprint': profile_obj.audio_fingerprint,
                'screen_fingerprint': profile_obj.screen_fingerprint,
            }
            profile_obj.last_used_at = datetime.utcnow()
            db.commit()

        logger.info(f"🔍 Search click-through: profile {profile_id}, keyword '{keyword}', domain '{domain}'")
        if task_id:
            _update_search_task_log(task_id, f"� Профиль {profile_data_from_db['name']}, ключ '{keyword}', домен '{domain}'")

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
        no_proxy = params.get('no_proxy', False)
        exclude_proxy_ids = params.get('exclude_proxy_ids', [])
        if no_proxy:
            logger.info("🚫 no_proxy mode — running without proxy")
            proxy_data = None
        elif profile_data_from_db['proxy_host'] and profile_data_from_db['proxy_port']:
            proxy_data = {
                'host': profile_data_from_db['proxy_host'],
                'port': profile_data_from_db['proxy_port'],
                'username': profile_data_from_db['proxy_username'],
                'password': profile_data_from_db['proxy_password'],
                'proxy_type': profile_data_from_db['proxy_type'] or 'http'
            }
        else:
            proxy_data = proxy_manager.get_available_proxy(exclude_ids=exclude_proxy_ids or None)
            if exclude_proxy_ids:
                logger.info(f"🔄 Retry: выбран новый прокси (исключены ID: {exclude_proxy_ids})")

        if not proxy_data and not no_proxy:
            error_msg = "🚫 Нет доступных прокси! Поиск без прокси запрещён."
            logger.error(error_msg)
            if task_id:
                _update_search_task_log(task_id, error_msg, status='failed')
            return {'status': 'error', 'error': error_msg, 'profile_id': profile_id}

        # Create browser session
        from core.profile_generator import ProfileGenerator
        profile_generator = ProfileGenerator()
        
        is_mobile = profile_data_from_db.get('is_mobile', False)
        
        profile_data = profile_generator.generate_profile(profile_data_from_db['name'], is_mobile=is_mobile)
        profile_data.update({
            'user_agent': profile_data_from_db['user_agent'],
            'viewport': {
                'width': profile_data_from_db['viewport_width'],
                'height': profile_data_from_db['viewport_height']
            },
            'timezone': profile_data_from_db['timezone'],
            'language': 'ru-RU',
            'platform': profile_data_from_db.get('platform') or profile_data.get('platform', 'Win32'),
            'images_enabled': False,  # Disable images for speed
        })

        # Use stored fingerprint data from DB (consistent across sessions)
        _db_webgl = profile_data_from_db.get('webgl_fingerprint')
        if _db_webgl:
            import json as _json
            try:
                webgl_dict = _json.loads(_db_webgl) if isinstance(_db_webgl, str) else _db_webgl
                if webgl_dict and isinstance(webgl_dict, dict) and 'unmaskedVendor' in webgl_dict:
                    profile_data['webgl_fingerprint'] = webgl_dict
            except (ValueError, TypeError):
                pass
        if profile_data_from_db.get('canvas_fingerprint'):
            profile_data['canvas_fingerprint'] = profile_data_from_db['canvas_fingerprint']
        if profile_data_from_db.get('audio_fingerprint'):
            profile_data['audio_fingerprint'] = profile_data_from_db['audio_fingerprint']
        _db_screen = profile_data_from_db.get('screen_fingerprint')
        if _db_screen and isinstance(_db_screen, dict):
            for _key in ('css_media', 'feature_flags', 'audio_properties', 'speech_voices', 'sensor',
                         'connection_info', 'storage_quota', 'heap_size', 'system_colors',
                         'system_fonts', 'codecs', 'keyboard_layout', 'fonts'):
                if _key in _db_screen:
                    profile_data[_key] = _db_screen[_key]
            # Restore hardware fingerprint values for consistency across sessions
            for _hw_key in ('hardware_concurrency', 'device_memory', 'max_touch_points', 'do_not_track'):
                if _hw_key in _db_screen:
                    profile_data[_hw_key] = _db_screen[_hw_key]
        
        if is_mobile:
            logger.info(f"📱 Mobile profile detected: {profile_data_from_db['name']}")
        
        # Track profile dir for cleanup even if Chrome fails to start
        from app.config import settings as _settings
        _profile_dir_for_cleanup = os.path.join(_settings.browser_user_data_dir, profile_data['name'])

        _check_wall_clock(start_time, 'before browser launch', _watchdog)
        browser_id = browser_manager.create_browser_session(profile_data, proxy_data)
        driver = browser_manager.active_browsers[browser_id]
        driver.set_page_load_timeout(40)  # 40s instead of default 300s
        driver.set_script_timeout(15)  # Prevent execute_script from hanging on dead renderer

        # Start watchdog AFTER browser is created — it will kill Chrome/node-driver
        # if the task is stuck (no heartbeat for WATCHDOG_IDLE_TIMEOUT seconds)
        _watchdog.start(start_time, _profile_dir_for_cleanup)

        # Set heartbeat callback for captcha-solving code in yandex_maps.py
        from tasks.yandex_maps import set_captcha_heartbeat
        set_captcha_heartbeat(lambda label: _watchdog.heartbeat(label))

        # NOTE: Network.enable + setBlockedURLs moved AFTER first navigation
        # to avoid interfering with Playwright's internal Fetch.enable proxy auth handler.
        # Calling Network.enable before first navigation breaks HTTPS CONNECT tunnel auth.

        # === Step 0: Entry point — 50/50 direct ya.ru vs through mail.ru ===
        # Real users arrive at Yandex via different paths: direct, bookmarks, or from other sites
        _check_wall_clock(start_time, 'before entry point', _watchdog)
        _referrer_used = False
        _entry_method = 'direct'  # mail.ru disabled — causes 9min hangs when page.mouse/scroll blocks on dead renderer
        
        if _entry_method == 'mail.ru':
            logger.info(f"🔗 Entry via mail.ru (50% chance)")
            if task_id:
                _update_search_task_log(task_id, f"🔗 Заходим через mail.ru...")
            
            ref_loaded = _safe_get(driver, 'https://mail.ru', timeout=15, label="mail.ru entry")
            if ref_loaded == 'dead':
                logger.warning("💀 Browser died visiting mail.ru — recovering page...")
                if hasattr(driver, 'recover_page') and driver.recover_page():
                    logger.info("✅ Page recovered after mail.ru crash")
                else:
                    logger.error("❌ Cannot recover page after mail.ru crash")
                    raise Exception("Browser died visiting mail.ru, cannot recover")
            elif ref_loaded:
                _referrer_used = True
                # Browse mail.ru briefly like a real user
                time.sleep(random.uniform(2, 5))
                _human_mouse_move(driver, duration=random.uniform(0.5, 1.5))
                _human_scroll(driver, 1, 3)
                time.sleep(random.uniform(1, 3))
                logger.info(f"✅ mail.ru visited, now going to Yandex")
            else:
                logger.warning(f"⏱️ mail.ru timed out, going direct to Yandex")
                try:
                    driver.execute_cdp_cmd("Page.stopLoading")
                except Exception:
                    pass
        else:
            logger.info(f"🔗 Direct entry to ya.ru (50% chance)")
        
        # Wall-clock check after entry point — catch slow proxy hangs early
        _check_wall_clock(start_time, 'after entry point', _watchdog)
        
        if task_id:
            _update_search_task_log(task_id, "🌐 Открываем Яндекс...")

        # === Step 1: Open Yandex ===
        _check_wall_clock(start_time, 'before ya.ru', _watchdog)
        ya_loaded = _safe_get(driver, "https://ya.ru", timeout=40, label="ya.ru")
        if ya_loaded == 'dead':
            logger.warning("💀 Browser died navigating to ya.ru — recovering...")
            if hasattr(driver, 'recover_page') and driver.recover_page():
                logger.info("✅ Page recovered, retrying ya.ru...")
                ya_loaded = _safe_get(driver, "https://ya.ru", timeout=40, label="ya.ru recovery")
                if ya_loaded == 'dead':
                    raise Exception("Browser died twice navigating to ya.ru — proxy/browser broken")
            else:
                raise Exception("Browser died navigating to ya.ru, cannot recover")
        if not ya_loaded:
            logger.warning("⏱️ ya.ru timed out but continuing...")
        time.sleep(random.uniform(3, 6))

        # === Check if page actually rendered (JS executed) ===
        # If ya.ru timed out and title is empty, the renderer is likely dead/stuck.
        # Try refreshing once before proceeding.
        try:
            _init_title = driver.title or ''
        except Exception:
            _init_title = ''
        
        if not _init_title.strip():
            logger.warning("⚠️ ya.ru loaded but Title is EMPTY — page JS did not execute, retrying...")
            try:
                driver.execute_cdp_cmd("Page.stopLoading")
            except Exception:
                pass
            time.sleep(1)
            # If page is dead, recover first
            try:
                driver.execute_script("1")
            except Exception:
                logger.warning("💀 Page dead after empty title — recovering...")
                if hasattr(driver, 'recover_page') and driver.recover_page():
                    logger.info("✅ Page recovered")
                else:
                    raise Exception("Browser dead after ya.ru empty title, cannot recover")
            # Try loading ya.ru one more time
            ya_retry = _safe_get(driver, "https://ya.ru", timeout=40, label="ya.ru retry")
            if ya_retry == 'dead':
                logger.error("💀 Browser died again on ya.ru retry — recovering...")
                if hasattr(driver, 'recover_page') and driver.recover_page():
                    ya_retry = _safe_get(driver, "https://ya.ru", timeout=40, label="ya.ru retry2")
                    if ya_retry == 'dead':
                        raise Exception("Browser died 3 times navigating to ya.ru")
                else:
                    raise Exception("Browser dead on ya.ru retry, cannot recover")
            if ya_retry:
                time.sleep(random.uniform(3, 5))
            else:
                logger.warning("⏱️ ya.ru retry also timed out")
                time.sleep(2)
            
            try:
                _retry_title = driver.title or ''
            except Exception:
                _retry_title = ''
            
            if not _retry_title.strip():
                logger.error("💀 ya.ru Title still empty after retry — proxy/renderer dead, failing fast")
                raise Exception("Browser renderer dead: ya.ru loaded with empty title after 2 attempts (proxy too slow)")
            else:
                logger.info(f"✅ ya.ru retry worked: Title='{_retry_title}'")

        # Check for captcha
        from tasks.yandex_maps import detect_captcha_or_block, handle_yandex_protection
        
        # === LIGHTWEIGHT CAPTCHA DIAGNOSTICS (avoid killing renderer) ===
        try:
            current_url_debug = driver.current_url
        except Exception:
            current_url_debug = ''
        try:
            page_title_debug = driver.title
        except Exception:
            page_title_debug = ''
        logger.info(f"📋 [DIAG] After ya.ru load: URL={current_url_debug}, Title='{page_title_debug}'")
        if task_id:
            _update_search_task_log(task_id, f"📋 URL: {current_url_debug[:120]}, Title: '{page_title_debug}'")
        
        # Detect captcha from URL first (zero renderer cost)
        _url_path_lower = current_url_debug.lower().split('?')[0]
        _url_captcha = 'showcaptcha' in _url_path_lower or '/captcha' in _url_path_lower
        
        # Only read page_source if URL doesn't already tell us it's captcha
        page_src_lower = ''
        if not _url_captcha:
            try:
                page_src_lower = driver.page_source[:3000].lower()
            except Exception:
                pass
        
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
        
        if detect_captcha_or_block(driver):
            _check_wall_clock(start_time, 'before home captcha solve', _watchdog)
            logger.warning(f"🚨 Captcha detected on Yandex homepage! Types: {detected_types}")
            if task_id:
                _update_search_task_log(task_id, f"⚠️ Капча на главной Яндекса ({', '.join(detected_types) or 'unknown'}), решаем...")
            captcha_solver = CaptchaSolver()
            
            heavy_captcha_detected = any(t in detected_types for t in ('kaleidoscope', 'silhouette', 'advanced_captcha'))
            max_home_captcha_attempts = 1  # Fail fast — retry with new proxy is better than looping

            # Try solving captcha with limited attempts
            solved = False
            for captcha_attempt in range(1, max_home_captcha_attempts + 1):
                solve_start = time.time()
                _watchdog.heartbeat('home captcha solve')
                try:
                    solved = handle_yandex_protection(driver, captcha_solver, max_kaleidoscope_attempts=3)
                except SoftTimeLimitExceeded:
                    raise
                except Exception as _hp_err:
                    _hp_str = str(_hp_err)
                    # Browser death — fail fast, no point retrying on dead browser
                    if ('closed' in _hp_str.lower() or 'Target page' in _hp_str 
                        or 'Browser died' in _hp_str or 'PoW' in _hp_str):
                        logger.error(f"💀 Browser died during home captcha solving — failing fast: {_hp_str[:200]}")
                        raise
                    elif 'Timed out' in _hp_str or 'timeout' in _hp_str.lower():
                        logger.warning(f"⚠️ Renderer timeout in handle_yandex_protection (home captcha attempt {captcha_attempt}): {_hp_str[:200]}")
                        # Wait for renderer recovery before continuing
                        time.sleep(10)
                        try:
                            driver.execute_script("1")
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
                    # If page is dead, recover it first (page.url is cached, use evaluate)
                    page_alive = True
                    try:
                        driver.execute_script("1")
                    except Exception:
                        page_alive = False
                        logger.warning("⚠️ Page is dead, recovering via new tab...")
                        if hasattr(driver, 'recover_page') and driver.recover_page():
                            logger.info("✅ New page created in same browser context")
                            page_alive = True
                        else:
                            logger.error("❌ Could not recover page — stopping retries")
                            break
                    if page_alive:
                        try:
                            driver.get("https://ya.ru/")
                        except Exception as _ref_err:
                            _ref_str = str(_ref_err)
                            if 'Timed out' in _ref_str or 'timeout' in _ref_str.lower():
                                logger.warning("⚠️ Renderer timeout during refresh — waiting...")
                                time.sleep(10)
                            elif 'closed' in _ref_str.lower() or 'Target' in _ref_str:
                                logger.warning(f"⚠️ Page died during navigation: {_ref_str[:200]}")
                                if hasattr(driver, 'recover_page') and driver.recover_page():
                                    logger.info("✅ Recovered page, retrying navigation")
                                    try:
                                        driver.get("https://ya.ru/")
                                    except Exception:
                                        pass
                                else:
                                    break
                            else:
                                raise
                        time.sleep(random.uniform(3, 5))
                        if not detect_captcha_or_block(driver):
                            logger.info("🎉 Captcha disappeared after refresh!")
                            solved = True
                            break
            
            # Post-captcha screenshots disabled to save time
            
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

        # === Idle mouse movement on ya.ru before typing (natural user behavior) ===
        _human_mouse_move(driver, duration=random.uniform(0.8, 2.5))

        # === Step 2: Type keyword via keyboard emulation ===
        _check_wall_clock(start_time, 'before typing keyword', _watchdog)
        if task_id:
            _update_search_task_log(task_id, f"⌨️ Вводим запрос: '{keyword}'")

        logger.info(f"⌨️ Step 2: Typing keyword '{keyword}' into search input")
        logger.info(f"   Current URL: {driver.current_url}")
        logger.info(f"   Page title: {driver.title}")

        # Wait for page to be interactive before searching for input elements
        # ya.ru is a React SPA — inputs may not be in DOM immediately
        _primary_input_selectors = [
            'input[name="text"]', 'input.search3__input',
            'input.mini-suggest__input', 'input[role="searchbox"]',
            'textarea[name="text"]',
        ]
        _found_early = False
        try:
            for _psel in _primary_input_selectors:
                try:
                    loc = driver._page.locator(_psel)
                    loc.first.wait_for(state='visible', timeout=8000)
                    _found_early = True
                    logger.info(f"   ✅ Input ready (wait_for): '{_psel}'")
                    break
                except Exception:
                    continue
            if not _found_early:
                logger.info("   ⏳ No input visible yet, waiting for networkidle...")
                try:
                    driver._page.wait_for_load_state('networkidle', timeout=10000)
                except Exception:
                    pass
                time.sleep(1)
        except Exception as _wait_err:
            logger.warning(f"   ⚠️ Input pre-wait failed: {_wait_err}")

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
                
                # Diagnose: what element actually has focus after clicking textarea?
                try:
                    diag = driver.execute_script("""
                        var ae = document.activeElement;
                        if (!ae) return 'none';
                        return JSON.stringify({
                            tag: ae.tagName, name: ae.name||'', id: ae.id||'',
                            cls: (ae.className||'').substring(0,80),
                            ce: ae.contentEditable, type: ae.type||'',
                            val: (ae.value||'').substring(0,30),
                            txt: (ae.textContent||'').substring(0,30)
                        });
                    """)
                    logger.info(f"   [DIAG] Active element after click: {diag}")
                except Exception as diag_err:
                    logger.warning(f"   [DIAG] Active element check failed: {diag_err}")
                
                # Strategy 1: Use Playwright page.type() with selector (most reliable)
                # This re-finds the element, focuses it properly, then types
                typed_via_playwright = False
                input_selectors_pw = [
                    'input.mini-suggest__input',
                    'input.search3__input',
                    'textarea[name="text"]',
                    'input[name="text"]',
                    'input[role="searchbox"]',
                    'input[role="combobox"]',
                ]
                for sel in input_selectors_pw:
                    try:
                        # Check if element exists and is visible
                        count = driver._page.locator(sel).count()
                        if count > 0 and driver._page.locator(sel).first.is_visible():
                            logger.info(f"   Using Playwright type() on '{sel}'...")
                            driver._page.locator(sel).first.click()
                            time.sleep(0.3)
                            driver._page.keyboard.press("Control+a")
                            driver._page.keyboard.press("Backspace")
                            time.sleep(0.2)
                            driver._page.locator(sel).first.type(keyword, delay=random.randint(40, 90))
                            typed_via_playwright = True
                            break
                    except Exception:
                        continue
                
                if typed_via_playwright:
                    typing_succeeded = True
                else:
                    # Strategy 2: Fallback — fill() via Playwright (non-human but reliable)
                    logger.warning(f"   Playwright type() failed on all selectors, using fill()...")
                    for sel in input_selectors_pw:
                        try:
                            if driver._page.locator(sel).count() > 0:
                                driver._page.locator(sel).first.fill(keyword)
                                typing_succeeded = True
                                logger.info(f"   fill() succeeded on '{sel}'")
                                break
                        except Exception:
                            continue
                    
                    if not typing_succeeded:
                        # Strategy 3: JS setter on textarea
                        logger.warning(f"   fill() also failed, using JS native setter...")
                        driver.execute_script("""
                            var el = arguments[0];
                            var text = arguments[1];
                            el.focus();
                            var proto = el.tagName === 'TEXTAREA' 
                                ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                            var setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
                            setter.call(el, text);
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                        """, search_input, keyword)
                        typing_succeeded = True
                        logger.info(f"   JS setter applied for '{keyword}'")
            except Exception as type_err:
                logger.warning(f"   All typing methods failed: {type_err}")
            
            if typing_succeeded:
                logger.info(f"   Keyword typed. Waiting for suggestions...")
                time.sleep(random.uniform(1.0, 2.5))
                
                # Verify value in any relevant input element
                try:
                    current_value = driver.execute_script("""
                        var sels = ['input.mini-suggest__input', 'input.search3__input', 
                                    'textarea[name=text]', 'input[name=text]'];
                        for (var i = 0; i < sels.length; i++) {
                            var el = document.querySelector(sels[i]);
                            if (el && el.value) return el.value;
                        }
                        var ae = document.activeElement;
                        return ae ? (ae.value || ae.textContent || '') : '';
                    """) or ''
                    logger.info(f"   Input value after typing: '{current_value[:50]}'")
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
            
            # Stop any still-loading page via CDP (not JS — avoids hang if navigation pending)
            try:
                driver.execute_cdp_cmd("Page.stopLoading")
            except Exception:
                pass
            time.sleep(0.5)
            
            try:
                logger.info(f"   Search submitted. URL: {driver.current_url[:120]}")
            except Exception as _url_err:
                logger.warning(f"   Search submitted but cannot read URL (renderer busy): {str(_url_err)[:80]}")
            
            # Verify we're on search results page
            try:
                current_url = driver.current_url.lower()
            except Exception:
                # Renderer still busy — wait and retry
                logger.warning("   Renderer timeout reading URL, waiting 10s...")
                time.sleep(10)
                try:
                    driver.execute_cdp_cmd("Page.stopLoading")
                except Exception:
                    pass
                try:
                    current_url = driver.current_url.lower()
                except Exception as _retry_err:
                    logger.error(f"   Still cannot read URL after wait: {str(_retry_err)[:80]}")
                    raise Exception(f"Browser renderer dead: cannot read URL after search submit")
            
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
                    _fallback2_ok = False
                    try:
                        driver.set_page_load_timeout(60)
                        driver.get(f"https://ya.ru/search/?text={encoded}")
                        _fallback2_ok = True
                        time.sleep(random.uniform(4, 7))
                        logger.info(f"   After fallback URL: {driver.current_url[:120]}")
                    except TimeoutException:
                        logger.warning(f"   Fallback URL timed out — aborting task")
                    except Exception as e:
                        logger.warning(f"   Fallback URL error: {e}")
                    if not _fallback2_ok:
                        raise Exception("Search fallback navigation failed — proxy or renderer dead, will retry")
        else:
            # Last resort fallback: direct URL navigation
            logger.warning("⚠️ Could not find search input — using direct URL as fallback")
            encoded = quote_plus(keyword)
            fallback_ok = _safe_get(driver, f"https://ya.ru/search/?text={encoded}", timeout=60, label="search fallback")

            if not fallback_ok:
                # Fallback navigation failed (timeout or network error).
                # Don't attempt recovery: 20+ blocking Playwright calls on dead
                # renderer/proxy accumulate to 6+ minutes of hanging.
                # Bail immediately — Celery will retry with a fresh proxy.
                logger.error("💀 Search fallback failed — aborting task (proxy/renderer dead)")
                raise Exception("Search fallback navigation failed — proxy or renderer dead, will retry")

            time.sleep(random.uniform(3, 5))
            # Wait for search results to render
            try:
                driver._page.wait_for_selector('[data-cid]', timeout=10000)
            except Exception:
                pass

        # Check wall clock after all search input attempts
        _check_wall_clock(start_time, 'after search input', _watchdog)
        logger.info(f"⏱️ [TIMING] after search input check passed, elapsed={time.time()-start_time:.0f}s")

        # Reset page load timeout to reasonable value after search section
        # (_safe_get may have set it to 40s which makes refresh() hang on dead proxy)
        driver.set_page_load_timeout(15)

        # Check for captcha on search results
        # Stop loading via CDP (not JS) to avoid hanging on pending navigation
        try:
            logger.info("⏱️ [TIMING] calling Page.stopLoading via CDP")
            driver.execute_cdp_cmd("Page.stopLoading")
            logger.info("⏱️ [TIMING] Page.stopLoading done")
        except Exception as _ws_err:
            logger.warning(f"⏱️ [TIMING] Page.stopLoading failed: {_ws_err}")
        
        try:
            logger.info("⏱️ [TIMING] reading current_url")
            search_url_debug = driver.current_url
            logger.info(f"⏱️ [TIMING] current_url done: {search_url_debug[:80]}")
            logger.info("⏱️ [TIMING] reading title")
            search_title_debug = driver.title
            logger.info(f"⏱️ [TIMING] title done: {search_title_debug[:50]}")
        except Exception as _diag_err:
            logger.warning(f"⚠️ Renderer timeout reading search page state: {str(_diag_err)[:80]}")
            time.sleep(5)
            try:
                driver.execute_cdp_cmd("Page.stopLoading")
            except Exception:
                pass
            try:
                search_url_debug = driver.current_url
                search_title_debug = driver.title
            except Exception:
                raise Exception("Browser renderer dead: cannot read URL after search")
        
        logger.info(f"📋 [DIAG] Search results page: URL={search_url_debug[:150]}, Title='{search_title_debug}'")
        
        # Skip heavy screenshot here to preserve renderer health
        
        # === Force refresh if page didn't render (Title='' or Title='Яндекс') ===
        _check_wall_clock(start_time, 'before refresh loop', _watchdog)
        logger.info(f"⏱️ [TIMING] before refresh loop, elapsed={time.time()-start_time:.0f}s, title='{search_title_debug[:30]}'")
        if search_title_debug.strip() in ('', 'Яндекс') and not detect_captcha_or_block(driver):
            for _refresh_attempt in range(1, 2):
                logger.warning(f"🔄 Empty page detected (Title='{search_title_debug}'), forced refresh #{_refresh_attempt}...")
                _check_wall_clock(start_time, f'refresh loop #{_refresh_attempt}', _watchdog)
                try:
                    # Stop pending loads before refresh via CDP (not JS — avoids navigation hang)
                    try:
                        driver.execute_cdp_cmd("Page.stopLoading")
                    except Exception:
                        pass
                    time.sleep(0.5)
                    logger.info("⏱️ [TIMING] calling driver.refresh()")
                    driver.refresh()
                    logger.info("⏱️ [TIMING] driver.refresh() done")
                    time.sleep(random.uniform(4, 7))
                    try:
                        WebDriverWait(driver, 15).until(
                            lambda d: len(d.find_elements(By.CSS_SELECTOR, '[data-cid]')) >= 3
                        )
                    except TimeoutException:
                        pass
                    _new_title = driver.title
                    _new_cid = len(driver.find_elements(By.CSS_SELECTOR, '[data-cid]'))
                    logger.info(f"🔄 After refresh #{_refresh_attempt}: Title='{_new_title}', data-cid={_new_cid}")
                    if _new_cid >= 3 or (_new_title.strip() not in ('', 'Яндекс') and 'captcha' not in _new_title.lower()):
                        search_title_debug = _new_title
                        break
                    if detect_captcha_or_block(driver):
                        logger.warning(f"🚨 Captcha appeared after refresh #{_refresh_attempt}")
                        break
                except Exception as _ref_err:
                    logger.warning(f"⚠️ Refresh #{_refresh_attempt} error: {_ref_err}")
                    break

        # === Detect dead browser/proxy: still on ya.ru/ with empty Title after all retries ===
        _check_wall_clock(start_time, 'after refresh loop', _watchdog)
        try:
            _post_refresh_url = driver.current_url
            _post_refresh_title = driver.title
        except Exception as _ctx_err:
            # PoW JS redirect may destroy execution context — retry once after short wait
            logger.warning(f"⚠️ Execution context lost (likely PoW redirect), retrying: {_ctx_err}")
            time.sleep(2)
            try:
                _post_refresh_url = driver.current_url
                _post_refresh_title = driver.title
            except Exception:
                # If still failing, use cached values
                _post_refresh_url = search_url_debug
                _post_refresh_title = search_title_debug
        if (_post_refresh_title.strip() in ('', 'Яндекс') 
                and '/search' not in _post_refresh_url.lower()
                and not detect_captcha_or_block(driver)):
            logger.error(f"💀 Browser/proxy is dead — page never loaded. URL={_post_refresh_url}, Title='{_post_refresh_title}'")
            raise Exception(f"Browser renderer dead: page never loaded (URL={_post_refresh_url}, Title='{_post_refresh_title}')")

        # Detect captcha from URL first (cheap), then page_source only if needed
        _search_url_lower = search_url_debug.lower().split('?')[0]
        _search_url_captcha = 'showcaptcha' in _search_url_lower or '/captcha' in _search_url_lower
        
        search_src_lower = ''
        if not _search_url_captcha:
            try:
                search_src_lower = driver.page_source[:3000].lower()
            except Exception:
                pass
        
        search_captcha_indicators = {
            'showcaptcha_url': 'showcaptcha' in _search_url_lower,
            'captcha_url': '/captcha' in _search_url_lower,
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
            # showcaptcha_url is fingerprint-based PoW — 1 attempt only, retry is useless
            # UNLESS Yandex switches from captchafast → regular showcaptcha (checkbox) after PoW fail
            # kaleidoscope/silhouette have better solve rates — give them 3 attempts
            is_showcaptcha = 'showcaptcha_url' in search_detected
            max_search_captcha_attempts = 1 if is_showcaptcha else 3

            # Try solving with limited attempts
            solved2 = False
            for search_captcha_attempt in range(1, max_search_captcha_attempts + 1):
                solve_start2 = time.time()
                _watchdog.heartbeat(f'search captcha attempt {search_captcha_attempt}')
                try:
                    solved2 = handle_yandex_protection(driver, captcha_solver, max_kaleidoscope_attempts=7)
                except SoftTimeLimitExceeded:
                    raise
                except Exception as _hp2_err:
                    _hp2_str = str(_hp2_err)
                    # Browser death — fail fast, no point retrying on dead browser
                    if ('closed' in _hp2_str.lower() or 'Target page' in _hp2_str 
                        or 'Browser died' in _hp2_str or 'PoW' in _hp2_str):
                        logger.error(f"💀 Browser died during search captcha solving — failing fast: {_hp2_str[:200]}")
                        raise
                    elif 'Timed out' in _hp2_str or 'timeout' in _hp2_str.lower():
                        logger.warning(f"⚠️ Renderer timeout in handle_yandex_protection (search captcha attempt {search_captcha_attempt}): {_hp2_str[:200]}")
                        time.sleep(10)
                        try:
                            driver.execute_script("1")
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

                # After showcaptchaFAST PoW failure, Yandex sometimes switches to regular
                # showcaptcha (checkbox → silhouette). Detect this and grant an extra attempt.
                if is_showcaptcha and max_search_captcha_attempts == 1:
                    try:
                        post_pow_url = driver.current_url.lower()
                        if 'captchafast' not in post_pow_url and ('showcaptcha' in post_pow_url or 'captcha' in driver.title.lower()):
                            logger.info("🔄 Yandex switched from captchaFAST → regular captcha after PoW fail — granting extra attempt")
                            max_search_captcha_attempts = 2  # allow one more loop iteration
                            is_showcaptcha = False  # next attempt will handle checkbox/silhouette
                            continue
                    except Exception:
                        pass
                    
                should_retry2 = search_captcha_attempt < max_search_captcha_attempts and solve_time2 < 90
                if should_retry2:
                    logger.info(f"🔄 Search captcha attempt {search_captcha_attempt} failed, refreshing for retry...")
                    if task_id:
                        _update_search_task_log(task_id, f"🔄 Попытка {search_captcha_attempt} не удалась, повтор...")
                    # Recover page if dead (page.url is cached so check with evaluate)
                    encoded_retry = quote_plus(keyword)
                    page_alive = True
                    try:
                        driver.execute_script("1")
                    except Exception:
                        page_alive = False
                        logger.warning("⚠️ Page is dead, recovering via new tab...")
                        if hasattr(driver, 'recover_page') and driver.recover_page():
                            logger.info("✅ New page created in same browser context")
                            page_alive = True
                        else:
                            logger.error("❌ Could not recover page — stopping retries")
                            break
                    if page_alive:
                        try:
                            # Navigate to ya.ru homepage first to break captcha redirect loop
                            driver.set_page_load_timeout(30)
                            driver.get("https://ya.ru")
                            time.sleep(random.uniform(2, 4))
                        except Exception as e_home:
                            logger.warning(f"⚠️ Homepage navigation error: {e_home}")
                            # If homepage fails too, try recovering page
                            if 'closed' in str(e_home).lower() or 'Target' in str(e_home):
                                if hasattr(driver, 'recover_page') and driver.recover_page():
                                    logger.info("✅ Recovered page after homepage error")
                                else:
                                    logger.error("❌ Could not recover page")
                                    break
                        try:
                            driver.set_page_load_timeout(40)
                            driver.get(f"https://ya.ru/search/?text={encoded_retry}")
                            time.sleep(random.uniform(4, 7))
                        except TimeoutException:
                            logger.warning("⚠️ Search page reload timed out")
                        except Exception as e:
                            logger.warning(f"⚠️ Search page reload error: {e}")
                            # Page likely died during navigation — recover
                            if hasattr(driver, 'recover_page') and driver.recover_page():
                                logger.info("✅ Recovered page after reload error")
                                try:
                                    driver.set_page_load_timeout(40)
                                    driver.get(f"https://ya.ru/search/?text={encoded_retry}")
                                    time.sleep(random.uniform(4, 7))
                                except Exception as e2:
                                    logger.warning(f"⚠️ Search reload still failed: {e2}")
                            else:
                                logger.error("❌ Could not recover page after reload error")
                                break
                        if not detect_captcha_or_block(driver):
                            logger.info("🎉 Search page loaded without captcha on retry!")
                            solved2 = True
                            break
            
            # Post-captcha screenshots disabled to save time
            
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

        # === Mouse movement over SERP before scanning results (natural behavior) ===
        _human_mouse_move(driver, duration=random.uniform(0.5, 1.5))

        # === Step 3: Find and click target ===
        _check_wall_clock(start_time, 'before find-and-click', _watchdog)
        result = _find_and_click_target(driver, domain, max_pages=max_pages, keyword=keyword,
                                        task_start_time=start_time)

        if not result['found']:
            # Check if the browser is actually on a captcha page — reclassify as captcha error
            _final_title = ''
            try:
                _final_title = (driver.title or '').lower()
            except Exception:
                pass
            _captcha_title_markers = ('верификация', 'вы не робот', 'подтвердите', 'captcha', 'robot')
            _final_url = ''
            try:
                _final_url = (driver.current_url or '').lower()
            except Exception:
                pass
            _is_actually_captcha = any(m in _final_title for m in _captcha_title_markers) or 'showcaptcha' in _final_url

            if _is_actually_captcha:
                # This is NOT a real "not found" — the page is a captcha
                error_msg = f"Captcha blocked search results (title: {_final_title[:60]})"
                logger.warning(f"🚨 Reclassifying not_found → captcha: {error_msg}")
                if task_id:
                    _update_search_task_log(task_id, f"❌ Капча заблокировала выдачу (не not_found)", status='failed', error=error_msg)
                _save_error_log(
                    task_id=task_id, profile_id=profile_id,
                    error_message=error_msg,
                    error_category='captcha',
                    keyword=keyword, domain=domain,
                    proxy_host=proxy_data.get('host') if proxy_data else None,
                    proxy_id=proxy_data.get('id') if proxy_data else None,
                    task_duration=int(time.time() - start_time)
                )
                return {
                    'status': 'error',
                    'error': error_msg,
                    'profile_id': profile_id,
                    'keyword': keyword,
                    'domain': domain,
                }

            # Save full debug dump: DOM + screenshot of final page state
            try:
                ts = int(time.time())
                _nf_url = driver.current_url or ''
                _nf_title = driver.title or ''
                logger.info(f"  📋 [NOT_FOUND DEBUG] Final URL: {_nf_url[:200]}")
                logger.info(f"  📋 [NOT_FOUND DEBUG] Final title: {_nf_title}")
                html_path = f"screenshots/search_notfound_{profile_id}_{ts}.html"
                page_src = driver.page_source or ''
                with open(html_path, 'w', encoding='utf-8') as f:
                    f.write(page_src)
                logger.info(f"  📄 NOT_FOUND page source: {html_path} ({len(page_src)} bytes)")
                try:
                    scr_path = f"screenshots/search_notfound_{profile_id}_{ts}.png"
                    driver.save_screenshot(scr_path)
                    logger.info(f"  📸 NOT_FOUND screenshot: {scr_path}")
                except Exception:
                    pass
            except Exception as dbg_err:
                logger.warning(f"  ⚠️ Debug dump failed: {dbg_err}")

            msg = f"❌ Сайт {domain} не найден в выдаче (проверено {max_pages} стр.)"
            logger.warning(msg)
            if task_id:
                _update_search_task_log(task_id, msg, status='not_found', error='Site not found in search results')
            
            _save_error_log(
                task_id=task_id, profile_id=profile_id,
                error_message='Site not found in search results',
                error_category='not_found',
                keyword=keyword, domain=domain,
                proxy_host=proxy_data.get('host') if proxy_data else None,
                proxy_id=proxy_data.get('id') if proxy_data else None,
                task_duration=int(time.time() - start_time)
            )
            
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
                profile_id=profile_id, task_id=task_id, clicked=False,
                referrer_used=_referrer_used
            )
            
            # Auto-disable keyword if it keeps failing
            _check_and_disable_keyword(target_id, keyword, domain, consecutive_threshold=10)
            
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
            
            _save_error_log(
                task_id=task_id, profile_id=profile_id,
                error_message=f'Click failed at page {result["page"]}, position {result["position"]}',
                error_category='click_failed',
                keyword=keyword, domain=domain,
                proxy_host=proxy_data.get('host') if proxy_data else None,
                proxy_id=proxy_data.get('id') if proxy_data else None,
                task_duration=int(time.time() - start_time)
            )
            # Save position history: found but click failed
            _save_position_history(
                search_target_id=target_id, keyword=keyword, domain=domain,
                found=True, page=result['page'], position=result['position'],
                profile_id=profile_id, task_id=task_id, clicked=False,
                serp_position=result.get('serp_position'),
                referrer_used=_referrer_used
            )
            return {'status': 'click_failed', **result}

        # === Step 4: Click completed — finish immediately ===
        total_time = time.time() - start_time
        actual_browse_time = 0

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

        # Update proxy stats — success
        try:
            if proxy_data and proxy_data.get('id'):
                proxy_manager.update_proxy_stats(proxy_data['id'], True, response_time=total_time * 1000)
        except Exception:
            pass

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

        # Retire profile only if it has clicked ALL active targets
        _retire_profile_after_search(profile_id, target_id)

        # Save position history: success
        _save_position_history(
            search_target_id=target_id, keyword=keyword, domain=domain,
            found=True, page=result['page'], position=result['position'],
            profile_id=profile_id, task_id=task_id, clicked=True,
            browse_time=round(actual_browse_time, 1),
            serp_position=result.get('serp_position'),
            referrer_used=_referrer_used
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
        _save_error_log(
            task_id=task_id, profile_id=profile_id,
            error_message='SoftTimeLimitExceeded',
            error_category='timeout',
            keyword=keyword, domain=domain,
            proxy_host=proxy_data.get('host') if proxy_data else None,
            proxy_id=proxy_data.get('id') if proxy_data else None,
            task_duration=int(time.time() - start_time)
        )
        raise

    except _WatchdogTimeout:
        # Watchdog killed Chrome and injected this exception.
        # Don't try to use browser/driver — everything is dead.
        logger.warning(f"⏰ Watchdog timeout caught for profile {profile_id}")
        if task_id:
            try:
                _update_search_task_log(task_id, "⏰ Watchdog: задача превысила лимит времени",
                                        status='failed', error='Watchdog: task timeout, processes killed',
                                        exec_time=int(time.time() - start_time))
            except Exception:
                pass
        return {'status': 'error', 'error': 'Watchdog: task timeout, processes killed', 'profile_id': profile_id}

    except Exception as e:
        error_str = str(e)
        logger.error(f"Error in search click-through for profile {profile_id}: {e}")
        
        # Save error debug dump (screenshot + URL)
        try:
            if browser_manager and browser_id:
                _err_driver = browser_manager.get_driver(browser_id)
                if _err_driver:
                    ts = int(time.time())
                    _err_url = _err_driver.current_url or ''
                    _err_title = _err_driver.title or ''
                    logger.info(f"  📋 [ERROR DEBUG] URL: {_err_url[:200]}, Title: {_err_title}")
                    try:
                        _err_driver.save_screenshot(f"screenshots/search_error_{profile_id}_{ts}.png")
                        logger.info(f"  📸 Error screenshot saved")
                    except Exception:
                        pass
                    try:
                        with open(f"screenshots/search_error_{profile_id}_{ts}.html", 'w', encoding='utf-8') as f:
                            f.write(_err_driver.page_source or '')
                        logger.info(f"  📄 Error DOM saved")
                    except Exception:
                        pass
        except Exception:
            pass
        
        # Retry on browser session crashes (invalid session id / browser window not found / renderer dead / browser died)
        _browser_dead = (
            'invalid session id' in error_str
            or 'Browser window not found' in error_str
            or 'Browser crashed during profile setup' in error_str
            or 'Browser renderer dead' in error_str
            or 'Browser died' in error_str
            or 'Browser dead' in error_str
            or 'Target page, context or browser has been closed' in error_str
            or 'cannot recover' in error_str
        )
        if _browser_dead:
            logger.warning(f"🔄 Browser session crashed/dead — will retry (profile {profile_id})")
            if task_id:
                _update_search_task_log(task_id, f"🔄 Браузер/прокси не работает, повторяем с другим прокси...", status='retry')
            # Close current browser before retry
            if browser_manager and browser_id:
                try:
                    browser_manager.close_browser_session(browser_id)
                    browser_id = None
                except Exception:
                    pass
            # Exclude failed proxy on retry
            retry_params = dict(params) if params else {}
            failed_proxy_id = proxy_data.get('id') if proxy_data else None
            if failed_proxy_id:
                retry_params['exclude_proxy_ids'] = list(set(exclude_proxy_ids + [failed_proxy_id]))
            try:
                raise self.retry(exc=e, countdown=15, max_retries=2,
                                 args=[profile_id, target_id, keyword, task_id, retry_params])
            except self.MaxRetriesExceededError:
                logger.error(f"Max retries exceeded for browser crash (profile {profile_id})")

        # Retry on captcha failures with different proxy (captcha = proxy/fingerprint flagged)
        # BUT: showcaptcha (PoW) failures are fingerprint-based — retrying with new proxy won't help
        # because the headless browser fingerprint is what gets rejected, not the IP
        _captcha_fail = (
            'Captcha not solved' in error_str
            or 'Captcha blocked' in error_str
            or 'Captcha reappeared' in error_str
        )
        _is_showcaptcha_fail = 'showcaptcha' in error_str
        if _captcha_fail:
            if _is_showcaptcha_fail:
                # showcaptcha = fingerprint rejected by Yandex PoW — retry is useless
                logger.warning(f"❌ showcaptcha failure — NOT retrying (fingerprint rejected, profile {profile_id})")
                if task_id:
                    _update_search_task_log(task_id, f"❌ showcaptcha не решена (fingerprint отклонён), без повтора")
                # Don't retry — fall through to generic error handling
            else:
                # Other captcha types (kaleidoscope, silhouette) — retry may help
                logger.warning(f"🔄 Captcha failure — will retry with different proxy (profile {profile_id})")
                if task_id:
                    _update_search_task_log(task_id, f"🔄 Капча не решена, повтор с другим прокси через 60с...", status='retry')
                # Close current browser before retry
                if browser_manager and browser_id:
                    try:
                        browser_manager.close_browser_session(browser_id)
                        browser_id = None
                    except Exception:
                        pass
                # Exclude current proxy on retry
                retry_params = dict(params) if params else {}
                failed_proxy_id = proxy_data.get('id') if proxy_data else None
                if failed_proxy_id:
                    retry_params['exclude_proxy_ids'] = list(set(exclude_proxy_ids + [failed_proxy_id]))
                try:
                    raise self.retry(exc=e, countdown=60, max_retries=2,
                                     args=[profile_id, target_id, keyword, task_id, retry_params])
                except self.MaxRetriesExceededError:
                    logger.error(f"Max retries exceeded for captcha failure (profile {profile_id})")

        # Retry on proxy tunnel failures (ERR_TUNNEL_CONNECTION_FAILED)
        if 'ERR_TUNNEL_CONNECTION_FAILED' in error_str or 'ERR_PROXY_CONNECTION_FAILED' in error_str \
                or 'Chrome network error' in error_str:
            logger.warning("🔄 Proxy tunnel failed — will retry with different proxy")
            if task_id:
                _update_search_task_log(task_id, f"🔄 Прокси не работает, повторяем с другим прокси...", status='retry')
            # Close current browser before retry
            if browser_manager and browser_id:
                try:
                    browser_manager.close_browser_session(browser_id)
                    browser_id = None
                except Exception:
                    pass
            # Exclude failed proxy on retry
            retry_params = dict(params) if params else {}
            failed_proxy_id = proxy_data.get('id') if proxy_data else None
            if failed_proxy_id:
                retry_params['exclude_proxy_ids'] = list(set(exclude_proxy_ids + [failed_proxy_id]))
            try:
                raise self.retry(exc=e, countdown=10, max_retries=2,
                                 args=[profile_id, target_id, keyword, task_id, retry_params])
            except self.MaxRetriesExceededError:
                logger.error("Max retries exceeded for proxy tunnel failure")
        
        if task_id:
            _update_search_task_log(task_id, f"❌ Ошибка: {error_str[:200]}", status='failed', error=error_str[:500])
        
        # Update proxy stats — failure
        try:
            if proxy_data and proxy_data.get('id'):
                proxy_manager.update_proxy_stats(proxy_data['id'], False, error_message=error_str[:200])
        except Exception:
            pass

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

        _save_error_log(
            task_id=task_id, profile_id=profile_id,
            error_message=error_str[:500],
            error_detail=error_str,
            keyword=keyword, domain=domain,
            proxy_host=proxy_data.get('host') if proxy_data else None,
            proxy_id=proxy_data.get('id') if proxy_data else None,
            task_duration=int(time.time() - start_time)
        )
        return {'status': 'error', 'error': str(e), 'profile_id': profile_id}

    finally:
        # Cancel watchdog — cleanup is happening normally, no need to force-kill
        _watchdog.cancel()

        # Clear captcha heartbeat callback
        try:
            from tasks.yandex_maps import set_captcha_heartbeat
            set_captcha_heartbeat(None)
        except Exception:
            pass

        # Restore previous SIGUSR1 handler
        try:
            signal.signal(signal.SIGUSR1, _prev_sigusr1)
        except Exception:
            pass

        # Close browser
        # If watchdog fired, Chrome/node-driver are already dead.
        # Skip graceful close to avoid blocking on broken Playwright pipe.
        if _watchdog.fired:
            logger.info(f"🔒 Watchdog fired — skipping browser.close() (processes already killed)")
        elif browser_manager and browser_id:
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

        # Profile reuse: don't retire after each click, profiles are reusable
        # _retire_profile_after_search(profile_id)


# ======================== SCHEDULER ========================

@shared_task(name='tasks.yandex_search.schedule_search_visits')
def schedule_search_visits():
    """
    Automatic scheduler for Yandex Search click-through visits.
    Runs every 1 minute via celery beat. Maintains a buffer of pending tasks
    so workers always have work available (conveyor model, no idle gaps).
    """
    scheduler_logger = logging.getLogger(__name__ + '.scheduler')
    scheduler_logger.info("🔄 Starting Yandex Search visit scheduler")

    # Distributed lock — variables at function scope for cleanup in finally
    r = None
    lock_key = 'scheduler:schedule_search_visits:lock'
    try:
        import redis as _redis
        from app.config import settings as _s
        r = _redis.Redis(host=_s.redis_host, port=_s.redis_port)
        if not r.set(lock_key, '1', nx=True, ex=45):
            scheduler_logger.info("⏭️ Another search scheduler already running, skipping")
            return {'status': 'skipped', 'reason': 'duplicate', 'scheduled': 0}
    except Exception as le:
        scheduler_logger.warning(f"Could not acquire scheduler lock: {le}")

    # Don't flood the queue — check both Redis queue AND active DB tasks
    try:
        queue_len = r.llen('yandex_search') or 0
        if queue_len > 50:
            scheduler_logger.info(f"⏭️ yandex_search queue already has {queue_len} tasks, skipping")
            return {'status': 'skipped', 'reason': f'queue_full ({queue_len})', 'scheduled': 0}
    except Exception as qe:
        scheduler_logger.warning(f"Could not check queue length: {qe}")

    # ── Conveyor model: keep a buffer of pending tasks so workers never idle ──
    # concurrency=20 in docker-compose limits actual simultaneous workers
    # BUFFER_TARGET = how many pending tasks to maintain (above in_progress)
    # Workers finish a task → immediately grab next from buffer → no gap
    MAX_CONCURRENT_SEARCH_TASKS = 100
    BUFFER_TARGET = 10  # keep ~10 pending tasks ready for workers (concurrency=10)
    try:
        with get_db_session() as db:
            active_count = db.query(Task).filter(
                Task.task_type == 'yandex_search',
                Task.status.in_(['in_progress', 'pending']),
            ).count()
            pending_count = db.query(Task).filter(
                Task.task_type == 'yandex_search',
                Task.status == 'pending',
            ).count()
            if active_count >= MAX_CONCURRENT_SEARCH_TASKS:
                scheduler_logger.info(
                    f"⏭️ Already {active_count} active search tasks (limit={MAX_CONCURRENT_SEARCH_TASKS}), skipping"
                )
                # Still run cleanup below, but skip scheduling
                pass
            elif pending_count >= BUFFER_TARGET:
                scheduler_logger.info(
                    f"⏭️ Buffer full: {pending_count} pending tasks (target={BUFFER_TARGET}), skipping"
                )
                # Still run cleanup below, but skip scheduling
                pass
    except Exception as ce:
        active_count = 0
        pending_count = 0
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
            # Give it a grace period slightly above the task hard time limit (600s).
            in_progress_cutoff = now - timedelta(minutes=11)
            from sqlalchemy import or_
            stale_in_progress = db.query(Task).filter(
                Task.task_type == 'yandex_search',
                Task.status == 'in_progress',
                or_(
                    (Task.started_at.isnot(None)) & (Task.started_at < in_progress_cutoff),
                    (Task.started_at.is_(None)) & (Task.created_at < in_progress_cutoff),
                ),
            ).all()

            # Pending tasks may sit in Redis if workers are busy. Only clean them
            # if they are old enough that they surely won't be picked up.
            pending_cutoff = now - timedelta(minutes=15)
            stale_pending = db.query(Task).filter(
                Task.task_type == 'yandex_search',
                Task.status == 'pending',
                Task.created_at < pending_cutoff,
            ).all()

            stale_tasks = list(stale_in_progress) + list(stale_pending)

            # Retry tasks that Celery never picked up again — clean after 2 hours
            retry_cutoff = now - timedelta(hours=2)
            stale_retry = db.query(Task).filter(
                Task.task_type == 'yandex_search',
                Task.status == 'retry',
                Task.created_at < retry_cutoff,
            ).all()
            stale_tasks.extend(stale_retry)

            if stale_tasks:
                scheduler_logger.info(
                    f"🧹 Cleaning up stale tasks: in_progress>{in_progress_cutoff.isoformat()} ({len(stale_in_progress)}), "
                    f"pending>{pending_cutoff.isoformat()} ({len(stale_pending)}), "
                    f"retry>{retry_cutoff.isoformat()} ({len(stale_retry)})"
                )
                for st in stale_tasks:
                    old_status = st.status
                    st.status = 'failed'
                    if old_status == 'pending':
                        st.error_message = st.error_message or 'Task stuck in pending (auto-cleanup)'
                        st.add_log("🧹 Auto-cleaned: stuck in 'pending' for too long")
                    elif old_status == 'retry':
                        st.error_message = (st.error_message or '') + ' [auto-cleanup: stale retry]'
                        st.add_log("🧹 Auto-cleaned: stuck in 'retry' — Celery never retried")
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
            pending_count = db.query(Task).filter(
                Task.task_type == 'yandex_search',
                Task.status == 'pending',
            ).count()
    except Exception as ce2:
        scheduler_logger.warning(f"Could not re-check active task count after cleanup: {ce2}")

    # After cleanup, re-check: skip if hard limit reached OR buffer already full
    if active_count >= MAX_CONCURRENT_SEARCH_TASKS:
        return {
            'status': 'skipped',
            'reason': f'too_many_active ({active_count}/{MAX_CONCURRENT_SEARCH_TASKS})',
            'scheduled': 0
        }
    if pending_count >= BUFFER_TARGET:
        return {
            'status': 'skipped',
            'reason': f'buffer_full ({pending_count}/{BUFFER_TARGET} pending)',
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

            # Exclude profiles that already have pending/in_progress tasks
            # to prevent multiple concurrent tasks on the same Chrome profile
            busy_profile_rows = db.query(Task.profile_id).filter(
                Task.task_type == 'yandex_search',
                Task.status.in_(['in_progress', 'pending']),
                Task.profile_id.isnot(None),
            ).distinct().all()
            busy_profile_ids = set(row[0] for row in busy_profile_rows)
            free_profile_ids = [pid for pid in all_profile_ids if pid not in busy_profile_ids]

            scheduler_logger.info(
                f"✅ Found {len(all_profile_ids)} warmed profiles, "
                f"{len(busy_profile_ids)} busy, {len(free_profile_ids)} free"
            )

            if not free_profile_ids:
                scheduler_logger.warning("⚠️  All warmed profiles are busy with pending/in_progress tasks")
                return {'status': 'skipped', 'reason': 'all_profiles_busy', 'scheduled': 0}

            # Track profiles assigned in THIS scheduler run (across all targets)
            profiles_assigned_this_run = set()

            scheduled_count = 0
            # Conveyor: only add enough tasks to refill the buffer
            # slots_available = how many NEW pending tasks to add
            buffer_deficit = max(0, BUFFER_TARGET - pending_count)
            hard_limit_remaining = max(0, MAX_CONCURRENT_SEARCH_TASKS - active_count)
            slots_available = min(buffer_deficit, hard_limit_remaining)
            scheduler_logger.info(
                f"📊 Buffer: {pending_count} pending, deficit={buffer_deficit}, "
                f"active={active_count}, slots_available={slots_available}"
            )
            if slots_available <= 0:
                scheduler_logger.info("⏭️ No slots needed (buffer full)")
                return {'status': 'skipped', 'reason': 'buffer_full', 'scheduled': 0}
            current_time = datetime.utcnow()

            # ═══ Phase 1: Gather budget data for ALL targets ═══
            target_schedule_data = []
            for target in targets:
                try:
                    # Conveyor model: no interval gating — we schedule based on
                    # click budget only. Buffer is refilled every minute.

                    keywords = target.get_active_keywords_list()
                    disabled_kws = target.get_disabled_keywords_set()
                    if disabled_kws:
                        scheduler_logger.info(
                            f"🚫 {target.domain}: {len(disabled_kws)} keyword(s) auto-disabled, "
                            f"{len(keywords)} active"
                        )
                    if not keywords:
                        scheduler_logger.warning(f"⚠️ No active keywords for {target.domain}, skipping")
                        continue

                    # Calculate click budget for each keyword based on position history
                    sr = target.success_rate if target.total_visits >= 10 else 100.0
                    
                    # Load exact frequency data for frequency-based priority
                    freq_weights = {}
                    try:
                        from app.models.keyword_frequency import KeywordFrequency
                        freq_records = db.query(KeywordFrequency).filter(
                            KeywordFrequency.target_id == target.id
                        ).all()
                        exact_freqs = {}
                        for fr in freq_records:
                            if fr.freq_exact is not None and fr.freq_exact > 0:
                                exact_freqs[fr.keyword] = fr.freq_exact
                        if exact_freqs:
                            # Normalize: weight = freq / avg_freq
                            # High-freq gets > 1.0, low-freq gets < 1.0
                            # Subtle range: 0.7 .. 1.5 to keep clicks natural
                            avg_freq = sum(exact_freqs.values()) / len(exact_freqs)
                            if avg_freq > 0:
                                for fkw, fval in exact_freqs.items():
                                    raw_weight = fval / avg_freq
                                    freq_weights[fkw] = max(0.7, min(1.5, raw_weight))
                                scheduler_logger.info(
                                    f"📊 {target.domain}: freq weights loaded for {len(freq_weights)} keywords "
                                    f"(avg_exact={avg_freq:.0f})"
                                )
                    except Exception as fe:
                        scheduler_logger.warning(f"Could not load frequency weights for {target.domain}: {fe}")
                    
                    keyword_budgets = []
                    total_budget = 0
                    
                    # Count pending/in_progress tasks per keyword for this target
                    # to prevent creating duplicate tasks while previous ones
                    # haven't completed (race condition with search_position_history)
                    pending_per_keyword = {}
                    try:
                        pending_tasks = db.query(Task).filter(
                            Task.task_type == 'yandex_search',
                            Task.status.in_(['pending', 'in_progress']),
                        ).all()
                        for pt in pending_tasks:
                            p = pt.parameters or {}
                            if p.get('target_id') == target.id:
                                pk = p.get('keyword', '')
                                pending_per_keyword[pk] = pending_per_keyword.get(pk, 0) + 1
                    except Exception:
                        pass
                    
                    for kw in keywords:
                        fw = freq_weights.get(kw, 1.0)
                        kw_calc = _calculate_keyword_clicks(db, target.id, kw, target_success_rate=sr, freq_weight=fw)
                        # Subtract pending tasks from remaining to prevent double-scheduling
                        pending_kw = pending_per_keyword.get(kw, 0)
                        effective_done = kw_calc["today_done"] + pending_kw
                        remaining = max(0, kw_calc["clicks_per_day"] - effective_done)
                        keyword_budgets.append({
                            "keyword": kw,
                            "clicks_per_day": kw_calc["clicks_per_day"],
                            "today_done": effective_done,
                            "remaining": remaining,
                            "phase": kw_calc["phase"],
                            "position": kw_calc.get("current_position"),
                            "reason": kw_calc["reason"],
                            "freq_weight": fw,
                        })
                        total_budget += remaining

                    if total_budget <= 0:
                        # Keyword-level budgets exhausted — redistribute
                        # visits_per_day budget across keywords to keep pushing
                        today_done_total = sum(kb["today_done"] for kb in keyword_budgets)
                        daily_target = max(target.visits_per_day, 1)
                        remaining_daily_target = max(0, daily_target - today_done_total)

                        if remaining_daily_target <= 0:
                            # Position strategy says all keywords are done.
                            # Give each keyword at least 1 more click to keep probing.
                            remaining_daily_target = len(keyword_budgets)
                            scheduler_logger.info(
                                f"🔄 {target.domain}: budgets exhausted, daily target met "
                                f"({today_done_total}/{daily_target}) — forcing {remaining_daily_target} probe clicks"
                            )

                        scheduler_logger.info(
                            f"📈 {target.domain}: redistributing {remaining_daily_target} clicks "
                            f"across {len(keyword_budgets)} keywords"
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
                        # No hard cap — visits_per_day is the goal, not a suggestion
                        scale_factor = min(scale_factor, 20.0)
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

                    # Sort: truly new keywords first, then zero-click, then by remaining budget desc
                    candidates = [kb for kb in keyword_budgets if kb["remaining"] > 0]
                    if not candidates:
                        continue
                    # Tier 1: Never checked keywords (phase="start") — highest priority
                    never_checked = [kb for kb in candidates if kb["today_done"] == 0 and kb["phase"] == "start"]
                    # Tier 2: Checked before but not clicked today
                    zero_click = [kb for kb in candidates if kb["today_done"] == 0 and kb["phase"] != "start"]
                    # Tier 3: Already clicked today
                    has_clicks = [kb for kb in candidates if kb["today_done"] > 0]
                    random.shuffle(never_checked)  # randomize among new keywords
                    # Weighted shuffle: keywords with higher remaining budget get picked
                    # more often, but not exclusively — ensures all keywords get clicks
                    def weighted_shuffle(items):
                        if not items:
                            return items
                        result = []
                        pool = list(items)
                        while pool:
                            weights = [max(kb["remaining"], 1) for kb in pool]
                            total_w = sum(weights)
                            probs = [w / total_w for w in weights]
                            idx = random.choices(range(len(pool)), weights=probs, k=1)[0]
                            result.append(pool.pop(idx))
                        return result
                    zero_click = weighted_shuffle(zero_click)
                    has_clicks = weighted_shuffle(has_clicks)
                    ordered_candidates = never_checked + zero_click + has_clicks

                    # Log strategy summary
                    summary_parts = []
                    for kb in keyword_budgets[:5]:
                        pos_str = f"поз.{kb['position']}" if kb['position'] else "?"
                        summary_parts.append(f"{kb['keyword'][:20]}({pos_str}, {kb['today_done']}/{kb['clicks_per_day']}, {kb['phase']})")
                    scheduler_logger.info(
                        f"📊 {target.domain} strategy: total_budget={total_budget}, "
                        f"keywords: {', '.join(summary_parts)}"
                    )

                    # ── Profile selection: 1 profile = 1 click per target ──
                    # Exclude profiles that already clicked THIS target
                    # AND profiles busy with pending/in_progress tasks
                    # AND profiles already assigned in this scheduler run
                    already_clicked_ids = set(
                        row[0] for row in db.query(ProfileSearchVisit.profile_id).filter(
                            ProfileSearchVisit.search_target_id == target.id,
                            ProfileSearchVisit.status == 'completed',
                        ).all()
                    )
                    excluded_ids = already_clicked_ids | busy_profile_ids | profiles_assigned_this_run
                    available_ids = [pid for pid in free_profile_ids if pid not in excluded_ids]
                    random.shuffle(available_ids)

                    if not available_ids:
                        scheduler_logger.warning(
                            f"⚠️ No fresh profiles for {target.domain} "
                            f"(all {len(already_clicked_ids)} already clicked), skipping"
                        )
                        continue

                    scheduler_logger.info(
                        f"🔄 {target.domain}: {len(available_ids)} fresh profiles "
                        f"({len(already_clicked_ids)} already clicked)"
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

            # ═══ Phase 2: Round-robin interleaved scheduling ═══
            # IMPORTANT: tasks must be interleaved across domains to avoid
            # suspicious patterns (e.g. 3 clicks on same domain in a row).
            # We build per-target task lists, then round-robin pick from each.
            grand_total = sum(td['total_budget'] for td in target_schedule_data)
            random.shuffle(target_schedule_data)  # randomize starting order

            scheduler_logger.info(
                f"📊 Budget allocation: grand_total={grand_total}, slots={slots_available}, "
                f"targets={len(target_schedule_data)}"
            )

            # Build per-target task queues with proportional slot counts
            # When there's only 1 target, we also interleave by keyword
            # to avoid same keyword back-to-back (suspicious pattern).
            per_target_queues = []  # list of lists of (target, keyword_data, profile_id, search_params)
            for td in target_schedule_data:
                target = td['target']
                candidates = td['candidates']
                available_ids = td['available_ids']

                proportion = td['total_budget'] / grand_total if grand_total > 0 else 1.0 / len(target_schedule_data)
                target_slots = max(1, round(slots_available * proportion))
                # Cap per-domain tasks per scheduler run (conveyor: small batches, frequent refills)
                MAX_TASKS_PER_DOMAIN = 10
                target_slots = min(target_slots, len(candidates), len(available_ids), MAX_TASKS_PER_DOMAIN)

                scheduler_logger.info(
                    f"🎯 {target.domain}: budget={td['total_budget']}, "
                    f"allocating {target_slots} tasks (proportion={proportion:.1%})"
                )

                search_params = {
                    'max_search_pages': target.max_search_pages,
                    'min_time_on_site': target.min_time_on_site,
                    'max_time_on_site': target.max_time_on_site,
                }

                # Build per-keyword sub-queues for this target
                # Each keyword gets its own queue to enable keyword-level interleaving
                # IMPORTANT: each profile used at most ONCE (no modulo wrap-around)
                # Filter available_ids against profiles already assigned to other targets in this run
                target_available = [pid for pid in available_ids if pid not in profiles_assigned_this_run]
                target_slots = min(target_slots, len(target_available))
                keyword_queues = {}  # keyword -> list of tasks
                profile_idx = 0
                for i in range(target_slots):
                    if profile_idx >= len(target_available):
                        scheduler_logger.info(
                            f"⚠️ {target.domain}: ran out of free profiles after {i} tasks "
                            f"(had {len(target_available)} available)"
                        )
                        break
                    chosen = candidates[i]
                    kw = chosen['keyword']
                    profile_id = target_available[profile_idx]
                    profiles_assigned_this_run.add(profile_id)
                    profile_idx += 1
                    if kw not in keyword_queues:
                        keyword_queues[kw] = []
                    keyword_queues[kw].append((target, chosen, profile_id, search_params))

                # Each keyword becomes a separate queue for interleaving
                for kw, kw_queue in keyword_queues.items():
                    if kw_queue:
                        per_target_queues.append(kw_queue)

            # Round-robin interleave: pick one task from each target in turn.
            # Also ensure same keyword doesn't repeat consecutively.
            interleaved = []
            queue_indices = [0] * len(per_target_queues)
            while len(interleaved) < slots_available:
                added_this_round = False
                for qi, queue in enumerate(per_target_queues):
                    if queue_indices[qi] >= len(queue):
                        continue
                    interleaved.append(queue[queue_indices[qi]])
                    queue_indices[qi] += 1
                    added_this_round = True
                    if len(interleaved) >= slots_available:
                        break
                if not added_this_round:
                    break  # all queues exhausted

            total_tasks_planned = len(interleaved)

            scheduler_logger.info(
                f"🔀 Interleaved {total_tasks_planned} tasks across {len(per_target_queues)} keyword queues "
                f"(buffer refill: {pending_count}→{pending_count + total_tasks_planned} pending)"
            )

            updated_targets = set()
            for idx, (target, chosen, profile_id, search_params) in enumerate(interleaved):
                if scheduled_count >= slots_available:
                    scheduler_logger.info(f"⏭️ Global concurrency limit reached ({MAX_CONCURRENT_SEARCH_TASKS}), stopping")
                    break

                try:
                    keyword = chosen['keyword']

                    scheduler_logger.info(
                        f"  📝 [{idx+1}/{total_tasks_planned}] {target.domain} keyword='{keyword}' "
                        f"({chosen['reason']}, done {chosen['today_done']}/{chosen['clicks_per_day']})"
                    )

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
                            'profile_id': profile_id,
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
                        queue='yandex_search'
                    )

                    try:
                        task_record.celery_task_id = async_result.id
                    except Exception:
                        pass

                    scheduled_count += 1
                    updated_targets.add(target.id)
                    scheduler_logger.info(
                        f"✅ Scheduled search visit for {target.domain} keyword='{keyword}' "
                        f"profile={profile_id} phase={chosen['phase']}"
                    )

                except Exception as e:
                    scheduler_logger.error(
                        f"❌ Error scheduling task {idx+1}: {e}",
                        exc_info=True
                    )
                    continue

            # Conveyor model: last_visit_at no longer updated at scheduling time.
            # Tasks go straight to queue, no interval-based gating needed.
            try:
                db.commit()
            except Exception as commit_err:
                scheduler_logger.error(f"❌ Commit error in scheduler: {commit_err}")
                try:
                    db.rollback()
                except Exception:
                    pass

            scheduler_logger.info(f"✅ Search scheduler completed. Scheduled {scheduled_count} visits")

            # Release distributed lock
            if r:
                try:
                    r.delete(lock_key)
                except Exception:
                    pass

            return {
                'status': 'success',
                'targets_processed': len(targets),
                'scheduled': scheduled_count,
                'timestamp': current_time.isoformat()
            }

    except Exception as e:
        scheduler_logger.error(f"❌ Search scheduler error: {e}", exc_info=True)
        # Release distributed lock on error
        if r:
            try:
                r.delete(lock_key)
            except Exception:
                pass
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
