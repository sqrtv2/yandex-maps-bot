"""
Profile warmup tasks for training browser profiles.
Realistic browsing sessions that build history, cookies, and behavioral patterns.
"""
import time
import random
import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta

from celery import shared_task
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException, StaleElementReferenceException

from app.database import get_db_session, get_setting
from app.models import BrowserProfile, Task
from core import BrowserManager, ProxyManager, ProfileGenerator
from core.domain_manager import domain_manager
from core.warmup_url_manager import get_warmup_urls
from .celery_app import BaseTask

logger = logging.getLogger(__name__)

# === Warmup site pools ===

# Yandex ecosystem — MUST visit to build Yandex cookies/trust
YANDEX_ECOSYSTEM = [
    "https://ya.ru",
    "https://yandex.ru",
    "https://dzen.ru",
    "https://market.yandex.ru",
    "https://pogoda.yandex.ru",
    "https://news.yandex.ru",
    "https://music.yandex.ru",
    "https://kinopoisk.ru",
    "https://translate.yandex.ru",
    "https://yandex.ru/images",
]

# Popular Russian sites — build realistic browsing profile
POPULAR_RUSSIAN_SITES = [
    "https://vk.com",
    "https://mail.ru",
    "https://ok.ru",
    "https://rbc.ru",
    "https://lenta.ru",
    "https://ria.ru",
    "https://tass.ru",
    "https://gazeta.ru",
    "https://kommersant.ru",
    "https://avito.ru",
    "https://ozon.ru",
    "https://wildberries.ru",
    "https://habr.com",
    "https://pikabu.ru",
    "https://sports.ru",
    "https://hh.ru",
    "https://2gis.ru",
    "https://dns-shop.ru",
    "https://mvideo.ru",
    "https://drive2.ru",
    "https://banki.ru",
    "https://auto.ru",
    "https://ivi.ru",
    "https://kp.ru",
    "https://7ya.ru",
]

# General international sites
INTERNATIONAL_SITES = [
    "https://google.com",
    "https://youtube.com",
    "https://ru.wikipedia.org",
    "https://reddit.com",
    "https://github.com",
]

# Search queries for Yandex (realistic Russian)
YANDEX_SEARCH_QUERIES = [
    "погода москва сегодня",
    "курс доллара",
    "новости россия",
    "рецепт борща",
    "расписание электричек",
    "купить квартиру москва",
    "ремонт стиральной машины",
    "как оформить загранпаспорт",
    "отзывы стоматология рядом",
    "расписание кинотеатр",
    "кафе рядом со мной",
    "доставка еды",
    "запись к врачу онлайн",
    "автосервис отзывы",
    "фитнес клуб рядом",
    "туры в турцию 2025",
    "лучшие рестораны",
    "салон красоты отзывы",
    "ветеринарная клиника рядом",
    "детский сад запись",
    "аптека рядом",
    "химчистка рядом",
    "мастер на час",
    "юрист консультация бесплатно",
    "шиномонтаж рядом",
]

# Google search queries (mixed)
GOOGLE_SEARCH_QUERIES = [
    "best restaurants near me",
    "weather today",
    "python tutorial",
    "рецепт пиццы дома",
    "как выбрать ноутбук",
    "фильмы 2025",
    "онлайн переводчик",
    "калькулятор ипотеки",
    "как похудеть",
    "лучшие книги 2025",
]


def _build_warmup_site_list(profile_id: int, count: int = 20) -> List[str]:
    """Build a diverse site list with guaranteed Yandex ecosystem presence."""
    sites = []

    # 1. Always include 3-5 Yandex ecosystem sites (essential for Yandex cookies)
    yandex_count = random.randint(3, 5)
    sites.extend(random.sample(YANDEX_ECOSYSTEM, min(yandex_count, len(YANDEX_ECOSYSTEM))))

    # 2. Add 8-12 popular Russian sites
    russian_count = random.randint(8, 12)
    available_russian = [s for s in POPULAR_RUSSIAN_SITES if s not in sites]
    sites.extend(random.sample(available_russian, min(russian_count, len(available_russian))))

    # 3. Add 2-4 international sites
    intl_count = random.randint(2, 4)
    sites.extend(random.sample(INTERNATIONAL_SITES, min(intl_count, len(INTERNATIONAL_SITES))))

    # 4. Try to get URLs from DB/domain_manager (additional diversity)
    try:
        db_urls = get_warmup_urls(count=5, profile_id=profile_id, strategy="diverse")
        if db_urls:
            for url in db_urls:
                if url not in sites:
                    sites.append(url)
    except:
        pass

    # Trim to requested count, shuffle
    if len(sites) > count:
        # Keep first 3 Yandex sites guaranteed, shuffle rest
        yandex_guaranteed = sites[:min(3, yandex_count)]
        rest = sites[min(3, yandex_count):]
        random.shuffle(rest)
        sites = yandex_guaranteed + rest[:count - len(yandex_guaranteed)]

    random.shuffle(sites)
    return sites


def _smooth_scroll(driver, direction="down", distance=None):
    """Smooth human-like scroll using behavior:'smooth'."""
    if distance is None:
        distance = random.randint(200, 700)
    if direction == "up":
        distance = -distance

    steps = random.randint(3, 6)
    step_size = distance // steps
    for i in range(steps):
        driver.execute_script(f"window.scrollBy({{top: {step_size}, behavior: 'smooth'}});")
        time.sleep(random.uniform(0.03, 0.1))
    time.sleep(random.uniform(0.3, 0.8))


def _human_read_page(driver, min_time=5, max_time=25):
    """Simulate a human reading a page: scroll, pause, look around."""
    read_time = random.uniform(min_time, max_time)
    end_time = time.time() + read_time

    while time.time() < end_time:
        action = random.choices(
            ["scroll_down", "scroll_up", "pause", "mouse_move"],
            weights=[40, 10, 35, 15],
            k=1
        )[0]

        try:
            if action == "scroll_down":
                _smooth_scroll(driver, "down", random.randint(150, 500))
                time.sleep(random.uniform(0.5, 2.0))

            elif action == "scroll_up":
                _smooth_scroll(driver, "up", random.randint(100, 300))
                time.sleep(random.uniform(0.3, 1.0))

            elif action == "pause":
                # Just reading
                time.sleep(random.uniform(1.0, 4.0))

            elif action == "mouse_move":
                try:
                    viewport_w = driver.execute_script("return window.innerWidth")
                    viewport_h = driver.execute_script("return window.innerHeight")
                    body = driver.find_element(By.TAG_NAME, "body")
                    ActionChains(driver).move_to_element_with_offset(
                        body,
                        random.randint(50, max(51, viewport_w - 50)),
                        random.randint(50, max(51, viewport_h - 50))
                    ).perform()
                    time.sleep(random.uniform(0.2, 0.6))
                except:
                    pass
        except:
            time.sleep(0.5)


def _try_dismiss_cookies(driver):
    """Try to accept/dismiss cookie consent banners."""
    try:
        selectors = [
            "button[class*='cookie']", "button[class*='consent']",
            "button[class*='accept']", "a[class*='cookie']",
            "[data-testid*='cookie'] button", ".cookie-banner button",
            "#cookie-accept", ".js-cookie-accept",
            "button[class*='agree']", ".gdpr-accept",
        ]
        for sel in selectors:
            try:
                btns = driver.find_elements(By.CSS_SELECTOR, sel)
                for btn in btns[:2]:
                    if btn.is_displayed() and btn.size['height'] > 10:
                        ActionChains(driver).move_to_element(btn).pause(
                            random.uniform(0.2, 0.5)
                        ).click().perform()
                        time.sleep(random.uniform(0.3, 0.8))
                        return True
            except:
                continue
    except:
        pass
    return False


def _perform_yandex_search(driver, query: str) -> bool:
    """Perform a search on Yandex and browse results."""
    try:
        driver.get("https://yandex.ru")
        time.sleep(random.uniform(3, 6))

        # Find search input — try multiple selectors (ya.ru/yandex.ru change frequently)
        search_input = None
        for selector in [
            "input#text",
            "input[name='text']",
            "input.search3__input",
            "input.mini-suggest__input",
            "input[aria-label*='Запрос']",
            "input[aria-label*='Search']",
            "input.HeaderDesktopForm-Input",
            "input.input__control",
            "#search-input input",
            "textarea[name='text']",
        ]:
            try:
                elems = driver.find_elements(By.CSS_SELECTOR, selector)
                for elem in elems:
                    if elem.is_displayed():
                        search_input = elem
                        break
                if search_input:
                    break
            except:
                continue

        if not search_input:
            # Fallback: direct URL search
            logger.info("Yandex search input not found, using direct URL search")
            encoded_query = query.replace(' ', '+')
            driver.get(f"https://yandex.ru/search/?text={encoded_query}")
            time.sleep(random.uniform(3, 6))
            _human_read_page(driver, min_time=8, max_time=20)
            logger.info(f"🔍 Yandex search (URL) completed: '{query}'")
            return True

        # Click on input
        ActionChains(driver).move_to_element(search_input).pause(
            random.uniform(0.3, 0.7)
        ).click().perform()
        time.sleep(random.uniform(0.5, 1.0))

        # Type query character by character
        for char in query:
            search_input.send_keys(char)
            time.sleep(random.uniform(0.05, 0.2))

        time.sleep(random.uniform(0.8, 2.0))

        # Submit
        search_input.send_keys(Keys.RETURN)
        time.sleep(random.uniform(2, 5))

        # Browse results: scroll and read
        _human_read_page(driver, min_time=8, max_time=20)

        # Sometimes click on a search result (30% chance)
        if random.random() < 0.3:
            try:
                results = driver.find_elements(By.CSS_SELECTOR, "a.OrganicTitle-Link, li.serp-item a.link, .organic__url")
                safe_results = [r for r in results if r.is_displayed()]
                if safe_results:
                    chosen = random.choice(safe_results[:5])
                    ActionChains(driver).move_to_element(chosen).pause(
                        random.uniform(0.3, 0.8)
                    ).click().perform()
                    time.sleep(random.uniform(3, 8))
                    _human_read_page(driver, min_time=5, max_time=15)
                    # Go back
                    driver.back()
                    time.sleep(random.uniform(1, 3))
            except:
                pass

        logger.info(f"🔍 Yandex search completed: '{query}'")
        return True

    except Exception as e:
        logger.warning(f"Error in Yandex search: {e}")
        return False


def _perform_google_search_warmup(driver, query: str) -> bool:
    """Perform a search on Google and browse results."""
    try:
        driver.get("https://www.google.com")
        time.sleep(random.uniform(2, 4))

        # Dismiss consent if needed
        _try_dismiss_cookies(driver)
        time.sleep(random.uniform(0.5, 1.5))

        # Find search input
        search_input = None
        for selector in ["textarea[name='q']", "input[name='q']"]:
            try:
                elems = driver.find_elements(By.CSS_SELECTOR, selector)
                for elem in elems:
                    if elem.is_displayed():
                        search_input = elem
                        break
                if search_input:
                    break
            except:
                continue

        if not search_input:
            return False

        ActionChains(driver).move_to_element(search_input).pause(
            random.uniform(0.3, 0.6)
        ).click().perform()
        time.sleep(random.uniform(0.3, 0.8))

        for char in query:
            search_input.send_keys(char)
            time.sleep(random.uniform(0.05, 0.18))

        time.sleep(random.uniform(0.5, 1.5))
        search_input.send_keys(Keys.RETURN)
        time.sleep(random.uniform(2, 5))

        _human_read_page(driver, min_time=5, max_time=15)

        logger.info(f"🔍 Google search completed: '{query}'")
        return True

    except Exception as e:
        logger.warning(f"Error in Google search: {e}")
        return False


def _visit_site_with_actions(driver, url: str, site_index: int, total_sites: int) -> float:
    """Visit a site and perform realistic human actions. Returns time spent."""
    visit_start = time.time()

    try:
        # Try to dismiss cookie banners
        _try_dismiss_cookies(driver)
        time.sleep(random.uniform(0.5, 1.5))

        # Decide how long to stay based on site type
        if any(y in url for y in ["yandex", "ya.ru", "dzen.ru", "kinopoisk"]):
            min_time, max_time = 10, 35  # Spend more time on Yandex ecosystem
        elif any(s in url for s in ["vk.com", "ok.ru", "youtube", "pikabu", "habr"]):
            min_time, max_time = 8, 25  # Social/content sites
        elif any(s in url for s in ["ozon", "wildberries", "avito", "market", "dns-shop", "mvideo"]):
            min_time, max_time = 8, 30  # E-commerce: browse longer
        else:
            min_time, max_time = 5, 20  # General sites

        # Read the page (scroll, pause, mouse moves)
        _human_read_page(driver, min_time=min_time, max_time=max_time)

        # Sometimes click on internal links (20% chance)
        if random.random() < 0.2:
            try:
                links = driver.find_elements(By.CSS_SELECTOR, "a[href]")
                clickable_links = []
                current_domain = url.split("//")[-1].split("/")[0].replace("www.", "")
                for link in links:
                    try:
                        href = link.get_attribute("href") or ""
                        if (current_domain in href and link.is_displayed()
                                and link.size.get('height', 0) > 5
                                and not href.endswith(('.pdf', '.zip', '.exe', '.doc'))):
                            clickable_links.append(link)
                    except StaleElementReferenceException:
                        continue

                if clickable_links:
                    chosen_link = random.choice(clickable_links[:10])
                    ActionChains(driver).move_to_element(chosen_link).pause(
                        random.uniform(0.3, 0.8)
                    ).click().perform()
                    time.sleep(random.uniform(2, 5))
                    _human_read_page(driver, min_time=3, max_time=10)
                    # Go back
                    driver.back()
                    time.sleep(random.uniform(1, 2))
            except:
                pass

    except Exception as e:
        logger.debug(f"Minor error during site actions on {url}: {e}")

    return time.time() - visit_start


@shared_task(base=BaseTask, bind=True, max_retries=1, default_retry_delay=60, time_limit=900, soft_time_limit=840)
def warmup_profile_task(self, profile_id: int, duration_minutes: int = None, sites_list: List[str] = None):
    """
    Realistic warmup: visit 15-22 sites with human-like browsing behavior.
    - Yandex ecosystem sites (build cookies/trust)
    - Search queries on Yandex and Google
    - Smooth scrolling, mouse movements, pauses
    - Cookie consent dismissal
    - Internal link clicking
    - Total session: 5-12 minutes

    Args:
        profile_id: ID of the browser profile to warm up
        duration_minutes: Ignored (kept for API compat)
        sites_list: List of sites to visit (default: auto-generated)
    """
    browser_manager = None
    browser_id = None

    try:
        # Get profile from database and extract all needed data
        with get_db_session() as db:
            profile_obj = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
            if not profile_obj:
                raise ValueError(f"Profile {profile_id} not found")

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

            profile_obj.status = "warming_up"
            db.commit()

        # Build diverse site list with Yandex ecosystem
        sites_count = random.randint(15, 22)
        if not sites_list:
            sites_list = _build_warmup_site_list(profile_id, count=sites_count)

        logger.info(f"🔥 Realistic warmup profile {profile_id}: {len(sites_list)} sites with human behavior")

        # Initialize managers
        browser_manager = BrowserManager()
        proxy_manager = ProxyManager()
        proxy_manager.load_proxies_from_db()

        # Get proxy for profile
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
            proxy_data = proxy_manager.get_available_proxy()
            if proxy_data:
                logger.info(f"Using proxy for warmup: {proxy_data['host']}:{proxy_data['port']}")

        # Generate profile data for browser
        profile_generator = ProfileGenerator()
        profile_data = profile_generator.generate_profile(profile_name)
        profile_data.update({
            'user_agent': profile_user_agent or profile_data['user_agent'],
            'viewport': {
                'width': profile_viewport_width,
                'height': profile_viewport_height
            },
            'timezone': profile_timezone,
            'language': profile_language
        })

        # Create browser session (CDP fingerprint injection happens here)
        browser_id = browser_manager.create_browser_session(profile_data, proxy_data)
        driver = browser_manager.active_browsers.get(browser_id)
        if not driver:
            raise RuntimeError(f"Failed to get driver for session {browser_id}")

        logger.info(f"Created browser session {browser_id} for profile {profile_id}")

        # === REALISTIC WARMUP ===
        start_time = time.time()
        sites_visited = 0
        successful_visits = 0
        total_time_spent = 0
        searches_done = 0

        # Phase 1: Yandex search (build Yandex cookies FIRST)
        if random.random() < 0.85:  # 85% chance to do Yandex search
            query = random.choice(YANDEX_SEARCH_QUERIES)
            if _perform_yandex_search(driver, query):
                searches_done += 1
                total_time_spent += 15  # approximate
            time.sleep(random.uniform(2, 5))

        # Phase 2: Visit sites with realistic browsing
        consecutive_failures = 0
        for i, site_url in enumerate(sites_list):
            try:
                if browser_manager.navigate_to_url(browser_id, site_url, timeout=20):
                    sites_visited += 1
                    consecutive_failures = 0  # Reset on success

                    visit_time = _visit_site_with_actions(driver, site_url, i, len(sites_list))
                    total_time_spent += visit_time
                    successful_visits += 1

                    logger.info(f"✅ [{successful_visits}/{len(sites_list)}] {site_url} — {visit_time:.1f}s")

                    # Natural delay between sites (1-5 sec, sometimes longer)
                    if random.random() < 0.1:
                        time.sleep(random.uniform(5, 12))  # 10% chance: longer pause
                    else:
                        time.sleep(random.uniform(1, 4))

                else:
                    sites_visited += 1
                    consecutive_failures += 1
                    logger.warning(f"⚠️ Failed to load {site_url}, skipping")
                    time.sleep(random.uniform(1, 2))

                    # If proxy is dead (3+ consecutive failures), stop early
                    if consecutive_failures >= 3:
                        logger.warning(f"🛑 {consecutive_failures} consecutive failures — proxy likely dead, stopping warmup early")
                        break

            except Exception as site_error:
                logger.error(f"Error visiting {site_url}: {site_error}")
                consecutive_failures += 1
                time.sleep(1)

                if consecutive_failures >= 3:
                    logger.warning(f"🛑 {consecutive_failures} consecutive errors — stopping warmup early")
                    break
                continue

            # Mid-session: do a Google search (once, 30% chance)
            if i == len(sites_list) // 2 and random.random() < 0.3 and searches_done < 2:
                query = random.choice(GOOGLE_SEARCH_QUERIES)
                if _perform_google_search_warmup(driver, query):
                    searches_done += 1
                time.sleep(random.uniform(2, 4))

        # Phase 3: End with one more Yandex search (40% chance) to reinforce cookies
        if random.random() < 0.4 and searches_done < 2:
            query = random.choice(YANDEX_SEARCH_QUERIES)
            if _perform_yandex_search(driver, query):
                searches_done += 1
            time.sleep(random.uniform(1, 3))

        # Calculate results
        actual_duration = time.time() - start_time
        success_rate = (successful_visits / max(sites_visited, 1) * 100)

        # Update profile in database
        with get_db_session() as db:
            profile_obj = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
            if profile_obj:
                profile_obj.status = "warmed"
                profile_obj.warmup_completed = True
                profile_obj.warmup_sessions_count += 1
                profile_obj.warmup_time_spent += max(1, int(actual_duration / 60))
                profile_obj.last_used_at = datetime.utcnow()
                db.commit()

        result = {
            "status": "completed",
            "profile_id": profile_id,
            "duration_seconds": round(actual_duration, 1),
            "sites_visited": sites_visited,
            "successful_visits": successful_visits,
            "success_rate": round(success_rate, 1),
            "searches_performed": searches_done,
            "total_time_spent": round(total_time_spent, 1),
            "average_time_per_site": round(total_time_spent / max(successful_visits, 1), 1)
        }

        logger.info(
            f"🔥 Warmup DONE profile {profile_id} in {actual_duration:.0f}s: "
            f"{successful_visits}/{sites_visited} sites, {searches_done} searches, "
            f"avg {result['average_time_per_site']:.1f}s/site"
        )
        return result

    except Exception as e:
        logger.error(f"Error in warmup task for profile {profile_id}: {e}")

        try:
            with get_db_session() as db:
                profile_obj = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
                if profile_obj:
                    profile_obj.status = "error"
                    db.commit()
        except:
            pass

        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)
        raise e

    finally:
        if browser_manager and browser_id:
            try:
                browser_manager.close_browser_session(browser_id)
            except Exception as e:
                logger.error(f"Error closing browser session: {e}")


@shared_task(base=BaseTask, bind=True)
def warmup_multiple_profiles_task(self, profile_ids: List[int], duration_minutes: int = None):
    """
    Warm up multiple profiles in parallel.
    """
    try:
        logger.info(f"Starting warmup for {len(profile_ids)} profiles")

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


@shared_task(base=BaseTask, bind=True, time_limit=900, soft_time_limit=840)
def advanced_warmup_task(self, profile_id: int, warmup_strategy: Dict = None):
    """
    Advanced warmup with custom strategy — uses the same realistic browsing helpers.
    """
    if not warmup_strategy:
        warmup_strategy = {}

    browser_manager = None
    browser_id = None

    try:
        sites = warmup_strategy.get('sites', [])
        if not sites:
            sites = _build_warmup_site_list(profile_id, count=20)

        search_queries = warmup_strategy.get('search_queries', YANDEX_SEARCH_QUERIES[:5])

        logger.info(f"Starting advanced warmup for profile {profile_id} with {len(sites)} sites")

        with get_db_session() as db:
            profile_obj = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
            if not profile_obj:
                raise ValueError(f"Profile {profile_id} not found")
            profile_name = profile_obj.name
            profile_obj.status = "warming_up"
            db.commit()

        browser_manager = BrowserManager()
        proxy_manager = ProxyManager()
        proxy_manager.load_proxies_from_db()
        proxy_data = proxy_manager.get_available_proxy()

        profile_generator = ProfileGenerator()
        profile_data = profile_generator.generate_profile(profile_name)

        browser_id = browser_manager.create_browser_session(profile_data, proxy_data)
        driver = browser_manager.active_browsers.get(browser_id)
        if not driver:
            raise RuntimeError(f"Failed to get driver for session {browser_id}")

        results = {
            "sites_visited": 0,
            "searches_performed": 0,
            "total_time": 0,
        }

        start_time = time.time()

        # Phase 1: Yandex search
        if search_queries:
            query = random.choice(search_queries)
            if _perform_yandex_search(driver, query):
                results["searches_performed"] += 1
            time.sleep(random.uniform(2, 5))

        # Phase 2: Visit sites
        for i, site in enumerate(sites):
            try:
                if browser_manager.navigate_to_url(browser_id, site, timeout=20):
                    visit_time = _visit_site_with_actions(driver, site, i, len(sites))
                    results["sites_visited"] += 1
                    logger.info(f"✅ [{results['sites_visited']}/{len(sites)}] {site} — {visit_time:.1f}s")
                    time.sleep(random.uniform(1, 4))
            except Exception as e:
                logger.error(f"Error visiting {site}: {e}")
                continue

        results["total_time"] = round(time.time() - start_time, 1)

        # Update profile
        with get_db_session() as db:
            profile_obj = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
            if profile_obj:
                profile_obj.status = "warmed"
                profile_obj.warmup_completed = True
                profile_obj.warmup_sessions_count += 1
                profile_obj.warmup_time_spent += max(1, int(results["total_time"] / 60))
                profile_obj.last_used_at = datetime.utcnow()
                db.commit()

        logger.info(f"Advanced warmup completed for profile {profile_id}: {results}")
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


@shared_task(base=BaseTask)
def periodic_rewarmup():
    """
    Periodic re-warmup: picks profiles that haven't been warmed recently
    and schedules warmup tasks for them. Keeps profiles "fresh" with
    ongoing browsing history.
    Runs every 2 hours via Celery Beat.
    """
    try:
        now = datetime.utcnow()
        # Re-warmup profiles that haven't been used in 24+ hours
        stale_threshold = now - timedelta(hours=24)
        batch_size = 10  # Warmup 10 profiles per cycle

        with get_db_session() as db:
            stale_profiles = db.query(BrowserProfile).filter(
                BrowserProfile.warmup_completed == True,
                BrowserProfile.status.in_(["warmed", "created"]),
                (BrowserProfile.last_used_at < stale_threshold) | (BrowserProfile.last_used_at.is_(None))
            ).order_by(BrowserProfile.last_used_at.asc().nullsfirst()).limit(batch_size).all()

            if not stale_profiles:
                logger.info("📋 No stale profiles need re-warmup")
                return {"re_warmed": 0}

            profile_ids = [p.id for p in stale_profiles]

        # Schedule warmup tasks with staggered delays
        scheduled = 0
        for i, pid in enumerate(profile_ids):
            delay_seconds = i * random.randint(30, 60)
            eta = now + timedelta(seconds=delay_seconds)
            warmup_profile_task.apply_async(args=[pid], eta=eta, queue='warmup')
            scheduled += 1

        logger.info(f"🔄 Scheduled re-warmup for {scheduled} stale profiles: {profile_ids}")
        return {"re_warmed": scheduled, "profile_ids": profile_ids}

    except Exception as e:
        logger.error(f"Error in periodic_rewarmup: {e}")
        return {"error": str(e)}


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


@shared_task(base=BaseTask)
def auto_fix_stuck_processes():
    """
    Periodic health check — auto-fix stuck profiles and clean up.
    Runs every 10 minutes via Celery Beat.
    """
    fixed = 0
    try:
        now = datetime.utcnow()
        stuck_threshold = timedelta(minutes=15)

        with get_db_session() as db:
            # Fix profiles stuck in warming_up state
            stuck_profiles = db.query(BrowserProfile).filter(
                BrowserProfile.status == "warming_up",
                BrowserProfile.updated_at < (now - stuck_threshold)
            ).all()

            for p in stuck_profiles:
                p.status = "created" if not p.warmup_completed else "warmed"
                p.updated_at = now
                fixed += 1
                logger.warning(
                    f"🔧 Auto-fixed stuck profile {p.name} (id={p.id}): "
                    f"was warming_up since {p.updated_at}, reset to {p.status}"
                )

            # Fix stalled tasks (in_progress for too long)
            stalled_threshold = timedelta(minutes=40)
            stalled_tasks = db.query(Task).filter(
                Task.status == "in_progress",
                Task.started_at.isnot(None),
                Task.started_at < (now - stalled_threshold)
            ).all()

            for t in stalled_tasks:
                t.status = "failed"
                t.error_message = "Автоматически отменена: задача зависла (>40 мин)"
                t.completed_at = now
                fixed += 1
                logger.warning(f"🔧 Auto-cancelled stalled task {t.id}: {t.name}")

            if fixed:
                db.commit()
                logger.info(f"🔧 Auto-fix: исправлено {fixed} зависших процессов")

        # Cleanup orphaned Chrome processes
        try:
            from core.browser_manager import cleanup_orphaned_chrome
            killed = cleanup_orphaned_chrome()
            if killed:
                logger.info(f"🧹 Auto-cleanup: убито {killed} зависших Chrome-процессов")
        except Exception as e:
            logger.error(f"Auto-cleanup Chrome error: {e}")

    except Exception as e:
        logger.error(f"Error in auto_fix_stuck_processes: {e}")

    return {"fixed": fixed}