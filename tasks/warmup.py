"""
Profile warmup tasks for training browser profiles.
"""
import time
import random
import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta

from celery import shared_task
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

from app.database import get_db_session, get_setting
from app.models import BrowserProfile, Task
from core import BrowserManager, ProxyManager, ProfileGenerator
from core.domain_manager import domain_manager
from core.warmup_url_manager import get_warmup_urls
from .celery_app import BaseTask

logger = logging.getLogger(__name__)


@shared_task(base=BaseTask, bind=True, max_retries=1, default_retry_delay=60, time_limit=600, soft_time_limit=540)
def warmup_profile_task(self, profile_id: int, duration_minutes: int = None, sites_list: List[str] = None):
    """
    Warm up a browser profile by visiting various sites.

    Args:
        profile_id: ID of the browser profile to warm up
        duration_minutes: How long to run warmup (default from settings)
        sites_list: List of sites to visit (default from settings)
    """
    browser_manager = None
    browser_id = None

    try:
        # Get profile from database and extract all needed data
        with get_db_session() as db:
            profile_obj = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
            if not profile_obj:
                raise ValueError(f"Profile {profile_id} not found")

            # Extract all profile data we need
            profile_name = profile_obj.name
            profile_user_agent = profile_obj.user_agent
            profile_viewport_width = profile_obj.viewport_width
            profile_viewport_height = profile_obj.viewport_height
            profile_timezone = profile_obj.timezone
            profile_language = profile_obj.language
            profile_proxy_host = profile_obj.proxy_host
            profile_proxy_port = profile_obj.proxy_port
            profile_proxy_username = profile_obj.proxy_username
            profile_proxy_password = profile_obj.proxy_password
            profile_proxy_type = profile_obj.proxy_type or 'http'

            # Update profile status
            profile_obj.status = "warming_up"
            db.commit()

        # Get settings
        duration_minutes = duration_minutes or get_setting('warmup_duration_minutes', 30)

        # Получаем уникальный набор URL'ов для этого профиля из базы данных
        # Каждый профиль получит свой случайный набор сайтов для предотвращения одинаковых куки
        if not sites_list:
            count = random.randint(10, 15)  # Случайное количество сайтов от 10 до 15
            sites_list = get_warmup_urls(count=count, profile_id=profile_id, strategy="diverse")

            # Fallback к старой системе если нет URL в базе
            if not sites_list:
                logger.warning(f"No URLs from warmup_url_manager, falling back to domain_manager for profile {profile_id}")
                sites_list = domain_manager.get_random_domains_for_profile(
                    profile_id=profile_id,
                    count=count,
                    avoid_repeats=True
                )

        logger.info(f"Выбранные домены для профиля {profile_id}: {len(sites_list)} сайтов")

        min_page_time = get_setting('warmup_min_page_time', 30)
        max_page_time = get_setting('warmup_max_page_time', 300)

        logger.info(f"Starting warmup for profile {profile_id}, duration: {duration_minutes}min")

        # Initialize managers
        browser_manager = BrowserManager()
        proxy_manager = ProxyManager()
        proxy_manager.load_proxies_from_db()

        # Get proxy for profile if needed
        proxy_data = None
        if profile_proxy_host and profile_proxy_port:
            proxy_data = {
                'host': profile_proxy_host,
                'port': profile_proxy_port,
                'username': profile_proxy_username,
                'password': profile_proxy_password,
                'proxy_type': profile_proxy_type
            }
        else:
            # Get available proxy
            proxy_data = proxy_manager.get_available_proxy()
            if proxy_data:
                logger.info(f"Using proxy for warmup: {proxy_data['host']}:{proxy_data['port']}")

        # Generate profile data for browser
        profile_generator = ProfileGenerator()
        profile_data = profile_generator.generate_profile(profile_name)

        # Update profile data with database values
        profile_data.update({
            'user_agent': profile_user_agent or profile_data['user_agent'],
            'viewport': {
                'width': profile_viewport_width,
                'height': profile_viewport_height
            },
            'timezone': profile_timezone,
            'language': profile_language
        })

        # Create browser session
        browser_id = browser_manager.create_browser_session(profile_data, proxy_data)
        logger.info(f"Created browser session {browser_id} for profile {profile_id}")

        # Warmup execution
        start_time = time.time()
        end_time = start_time + (duration_minutes * 60)
        sites_visited = 0
        successful_visits = 0
        total_time_spent = 0

        while time.time() < end_time:
            try:
                # Select random site
                site_url = random.choice(sites_list)

                # Navigate to site
                if browser_manager.navigate_to_url(browser_id, site_url, timeout=30):
                    sites_visited += 1
                    visit_start = time.time()

                    # Perform human-like actions
                    actions = random.sample([
                        "scroll", "mouse_move", "click_random"
                    ], k=random.randint(1, 3))

                    browser_manager.perform_human_actions(browser_id, actions)

                    # Stay on page for random time
                    page_time = random.randint(min_page_time, max_page_time)
                    logger.info(f"Sleeping {page_time}s on page (settings: {min_page_time}-{max_page_time})")
                    time.sleep(page_time)

                    visit_time = time.time() - visit_start
                    total_time_spent += visit_time
                    successful_visits += 1

                    logger.info(f"Visited {site_url} for {visit_time:.1f}s")

                    # Random delay between sites
                    delay = random.randint(3, 10)
                    time.sleep(delay)

                else:
                    logger.warning(f"Failed to navigate to {site_url}")
                    time.sleep(10)  # Short delay before trying next site

                # Check if we should continue
                if time.time() >= end_time:
                    break

            except Exception as site_error:
                logger.error(f"Error visiting site {site_url}: {site_error}")
                time.sleep(10)  # Wait before trying next site
                continue

        # Calculate results
        actual_duration = time.time() - start_time
        success_rate = (successful_visits / sites_visited * 100) if sites_visited > 0 else 0

        # Update profile in database
        with get_db_session() as db:
            profile_obj = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
            if profile_obj:
                profile_obj.status = "warmed"
                profile_obj.warmup_completed = True
                profile_obj.warmup_sessions_count += 1
                profile_obj.warmup_time_spent += int(actual_duration / 60)  # Convert to minutes
                profile_obj.last_used_at = datetime.utcnow()

                db.commit()

        result = {
            "status": "completed",
            "profile_id": profile_id,
            "duration_minutes": actual_duration / 60,
            "sites_visited": sites_visited,
            "successful_visits": successful_visits,
            "success_rate": success_rate,
            "total_time_spent": total_time_spent,
            "average_time_per_site": total_time_spent / successful_visits if successful_visits > 0 else 0
        }

        logger.info(f"Warmup completed for profile {profile_id}: {result}")
        return result

    except Exception as e:
        logger.error(f"Error in warmup task for profile {profile_id}: {e}")

        # Update profile status on error
        try:
            with get_db_session() as db:
                profile_obj = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
                if profile_obj:
                    profile_obj.status = "error"
                    db.commit()
        except:
            pass

        # Retry task if possible
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)

        raise e

    finally:
        # Cleanup browser session
        if browser_manager and browser_id:
            try:
                browser_manager.close_browser_session(browser_id)
            except Exception as e:
                logger.error(f"Error closing browser session: {e}")


@shared_task(base=BaseTask, bind=True)
def warmup_multiple_profiles_task(self, profile_ids: List[int], duration_minutes: int = None):
    """
    Warm up multiple profiles in parallel.

    Args:
        profile_ids: List of profile IDs to warm up
        duration_minutes: Duration for each profile warmup
    """
    try:
        logger.info(f"Starting warmup for {len(profile_ids)} profiles")

        # Start warmup tasks for each profile
        task_ids = []
        for profile_id in profile_ids:
            task = warmup_profile_task.delay(profile_id, duration_minutes)
            task_ids.append({
                'profile_id': profile_id,
                'task_id': task.id
            })

        return {
            "status": "started",
            "profiles_count": len(profile_ids),
            "tasks": task_ids
        }

    except Exception as e:
        logger.error(f"Error starting multiple profile warmup: {e}")
        raise


@shared_task(base=BaseTask, bind=True)
def advanced_warmup_task(self, profile_id: int, warmup_strategy: Dict):
    """
    Advanced warmup with custom strategy.

    Args:
        profile_id: Profile to warm up
        warmup_strategy: Custom warmup configuration
    """
    browser_manager = None
    browser_id = None

    try:
        # Get strategy parameters
        sites = warmup_strategy.get('sites', [])

        # Если не указаны сайты в стратегии, получаем из нового warmup_url_manager
        if not sites:
            # Получаем разнообразные URL из базы данных
            sites = get_warmup_urls(count=15, profile_id=profile_id, strategy="diverse")

            # Fallback к старой системе если нет URL в базе
            if not sites:
                logger.warning(f"No URLs from warmup_url_manager, falling back to domain_manager categories for profile {profile_id}")
                categories = warmup_strategy.get('categories', ['social', 'news', 'search', 'ecommerce'])
                sites = domain_manager.get_domains_by_category(categories, count=15)
                logger.info(f"Получено {len(sites)} доменов по категориям {categories} для профиля {profile_id}")
            else:
                logger.info(f"Получено {len(sites)} URL'ов из warmup_url_manager для профиля {profile_id}")

        actions_per_site = warmup_strategy.get('actions_per_site', 3)
        min_time_per_site = warmup_strategy.get('min_time_per_site', 30)
        max_time_per_site = warmup_strategy.get('max_time_per_site', 300)
        search_queries = warmup_strategy.get('search_queries', [])
        form_interactions = warmup_strategy.get('form_interactions', False)

        logger.info(f"Starting advanced warmup for profile {profile_id}")

        # Get profile from database
        with get_db_session() as db:
            profile_obj = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
            if not profile_obj:
                raise ValueError(f"Profile {profile_id} not found")

            # Extract profile name
            profile_name = profile_obj.name

        # Initialize browser
        browser_manager = BrowserManager()
        proxy_manager = ProxyManager()
        proxy_manager.load_proxies_from_db()

        # Get proxy
        proxy_data = proxy_manager.get_available_proxy()

        # Create profile data
        profile_generator = ProfileGenerator()
        profile_data = profile_generator.generate_profile(profile_name)

        # Create browser session
        browser_id = browser_manager.create_browser_session(profile_data, proxy_data)

        results = {
            "sites_visited": [],
            "searches_performed": [],
            "forms_filled": [],
            "total_actions": 0
        }

        # Visit sites with advanced actions
        for site in sites:
            try:
                if browser_manager.navigate_to_url(browser_id, site):
                    site_result = {
                        "url": site,
                        "visit_time": 0,
                        "actions_performed": []
                    }

                    visit_start = time.time()

                    # Perform advanced actions
                    if "google.com" in site and search_queries:
                        # Perform search
                        query = random.choice(search_queries)
                        success = perform_google_search(browser_manager, browser_id, query)
                        if success:
                            site_result["actions_performed"].append(f"search: {query}")
                            results["searches_performed"].append(query)

                    elif form_interactions:
                        # Try to interact with forms
                        forms_filled = perform_form_interactions(browser_manager, browser_id)
                        if forms_filled:
                            site_result["actions_performed"].extend(forms_filled)
                            results["forms_filled"].extend(forms_filled)

                    # Standard actions
                    standard_actions = ["scroll", "mouse_move"]
                    if random.random() > 0.3:  # 70% chance for safe clicking
                        standard_actions.append("click_random")

                    browser_manager.perform_human_actions(browser_id, standard_actions)
                    site_result["actions_performed"].extend(standard_actions)

                    # Wait on page
                    page_time = random.randint(min_time_per_site, max_time_per_site)
                    time.sleep(page_time)

                    site_result["visit_time"] = time.time() - visit_start
                    results["sites_visited"].append(site_result)
                    results["total_actions"] += len(site_result["actions_performed"])

            except Exception as site_error:
                logger.error(f"Error in advanced warmup for site {site}: {site_error}")
                continue

        # Update profile
        with get_db_session() as db:
            profile_obj = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
            if profile_obj:
                profile_obj.status = "warmed"
                profile_obj.warmup_completed = True
                profile_obj.warmup_sessions_count += 1
                db.commit()

        logger.info(f"Advanced warmup completed for profile {profile_id}")
        return results

    except Exception as e:
        logger.error(f"Error in advanced warmup: {e}")
        raise

    finally:
        if browser_manager and browser_id:
            try:
                browser_manager.close_browser_session(browser_id)
            except Exception as e:
                logger.error(f"Error closing browser session: {e}")


def perform_google_search(browser_manager: BrowserManager, browser_id: str, query: str) -> bool:
    """Perform a Google search."""
    try:
        # Find search input
        driver = browser_manager.active_browsers.get(browser_id)
        if not driver:
            return False

        # Wait for search input to be available
        wait = WebDriverWait(driver, 10)
        search_input = wait.until(
            EC.presence_of_element_located((By.NAME, "q"))
        )

        # Clear and type query
        search_input.clear()
        for char in query:
            search_input.send_keys(char)
            time.sleep(random.uniform(0.1, 0.3))

        # Submit search
        search_input.submit()

        # Wait for results
        wait.until(EC.presence_of_element_located((By.ID, "search")))

        # Scroll through results
        for _ in range(random.randint(2, 5)):
            driver.execute_script("window.scrollBy(0, 300);")
            time.sleep(random.uniform(1, 3))

        return True

    except Exception as e:
        logger.warning(f"Error performing Google search: {e}")
        return False


def perform_form_interactions(browser_manager: BrowserManager, browser_id: str) -> List[str]:
    """Interact with forms on the page."""
    actions = []

    try:
        driver = browser_manager.active_browsers.get(browser_id)
        if not driver:
            return actions

        # Find input fields
        inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='text'], input[type='search'], input[type='email']")

        for input_elem in inputs[:3]:  # Limit to first 3 inputs
            try:
                if input_elem.is_displayed() and input_elem.is_enabled():
                    # Skip password and sensitive fields
                    input_type = input_elem.get_attribute("type") or ""
                    name = input_elem.get_attribute("name") or ""

                    if "password" in name.lower() or input_type == "password":
                        continue

                    # Type some test text
                    test_texts = ["test", "example", "demo", "hello"]
                    text = random.choice(test_texts)

                    input_elem.clear()
                    for char in text:
                        input_elem.send_keys(char)
                        time.sleep(random.uniform(0.1, 0.2))

                    actions.append(f"filled_input: {name or 'unnamed'}")
                    time.sleep(random.uniform(0.5, 2))

            except Exception as e:
                logger.debug(f"Error interacting with input: {e}")
                continue

    except Exception as e:
        logger.warning(f"Error in form interactions: {e}")

    return actions


@shared_task(base=BaseTask)
def schedule_profile_warmup(profile_id: int, delay_minutes: int = 0):
    """
    Schedule a profile warmup with delay.

    Args:
        profile_id: Profile to warm up
        delay_minutes: Minutes to wait before starting
    """
    try:
        if delay_minutes > 0:
            # Schedule warmup task with delay
            eta = datetime.utcnow() + timedelta(minutes=delay_minutes)
            task = warmup_profile_task.apply_async(args=[profile_id], eta=eta)
        else:
            # Start immediately
            task = warmup_profile_task.delay(profile_id)

        return {
            "status": "scheduled",
            "profile_id": profile_id,
            "task_id": task.id,
            "delay_minutes": delay_minutes
        }

    except Exception as e:
        logger.error(f"Error scheduling profile warmup: {e}")
        raise


@shared_task(base=BaseTask)
def get_warmup_status(profile_id: int) -> Dict:
    """Get current warmup status for a profile."""
    try:
        with get_db_session() as db:
            profile = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
            if not profile:
                return {"error": "Profile not found"}

            return {
                "profile_id": profile_id,
                "status": profile.status,
                "warmup_completed": profile.warmup_completed,
                "warmup_sessions_count": profile.warmup_sessions_count,
                "warmup_time_spent": profile.warmup_time_spent,
                "last_used_at": profile.last_used_at.isoformat() if profile.last_used_at else None,
                "is_ready_for_tasks": profile.is_ready_for_tasks()
            }

    except Exception as e:
        logger.error(f"Error getting warmup status: {e}")
        return {"error": str(e)}