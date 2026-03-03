"""
Profile warmup tasks for training browser profiles.
Realistic browsing sessions that build history, cookies, and behavioral patterns.
"""
import os
import time
import random
import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException, StaleElementReferenceException

from app.database import get_db_session, get_setting
from app.models import BrowserProfile, Task
from app.config import settings
from core import BrowserManager, ProxyManager, ProfileGenerator
from core.domain_manager import domain_manager
from core.warmup_url_manager import get_warmup_urls
from .celery_app import BaseTask

# Fast mode: reduce all delays by this factor for higher throughput
FAST_MODE = getattr(settings, 'fast_mode', False)
SPEED_FACTOR = 0.5 if FAST_MODE else 1.0  # 50% of normal time in fast mode

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

# Yandex Maps search queries — for stage 2-3 warmup (pre-browsing maps)
YANDEX_MAPS_SEARCH_QUERIES = [
    "кафе рядом",
    "аптека",
    "супермаркет рядом",
    "банкомат сбербанк",
    "заправка рядом",
    "парикмахерская рядом",
    "стоматология",
    "ветеринарная клиника",
    "шиномонтаж",
    "автосервис",
    "фитнес клуб",
    "детский сад рядом",
    "поликлиника",
    "ресторан",
    "пиццерия рядом",
    "химчистка",
    "ремонт телефонов",
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

# === Multi-session warmup configuration ===
# Number of sessions required before marking profile as fully warmed
MIN_WARMUP_SESSIONS = 3
# Minimum hours between first and last warmup session
MIN_WARMUP_HOURS_SPREAD = 2
# Hours between warmup sessions
WARMUP_SESSION_INTERVAL_HOURS = 1


def _build_warmup_site_list(profile_id: int, count: int = 20, stage: int = 1) -> List[str]:
    """Build a diverse site list based on warmup stage.
    
    Stage 1: General browsing + Yandex ecosystem (build cookies)
    Stage 2: More Yandex + first Yandex Maps exploration
    Stage 3: Yandex heavy + Yandex Maps organization searches
    Stage 4+: Reinforcement/maintenance
    """
    sites = []

    if stage == 1:
        # Stage 1: Foundation — Yandex cookies + general browsing
        yandex_count = random.randint(3, 5) if FAST_MODE else random.randint(4, 6)
        sites.extend(random.sample(YANDEX_ECOSYSTEM, min(yandex_count, len(YANDEX_ECOSYSTEM))))

        russian_count = random.randint(4, 7) if FAST_MODE else random.randint(8, 12)
        available_russian = [s for s in POPULAR_RUSSIAN_SITES if s not in sites]
        sites.extend(random.sample(available_russian, min(russian_count, len(available_russian))))

        intl_count = random.randint(1, 2) if FAST_MODE else random.randint(2, 4)
        sites.extend(random.sample(INTERNATIONAL_SITES, min(intl_count, len(INTERNATIONAL_SITES))))

    elif stage == 2:
        # Stage 2: Deepen Yandex trust + introduce Maps
        yandex_count = random.randint(5, 7)
        sites.extend(random.sample(YANDEX_ECOSYSTEM, min(yandex_count, len(YANDEX_ECOSYSTEM))))

        # Always include Yandex Maps main page
        if "https://yandex.ru/maps" not in sites:
            sites.append("https://yandex.ru/maps")

        russian_count = random.randint(5, 8)
        available_russian = [s for s in POPULAR_RUSSIAN_SITES if s not in sites]
        sites.extend(random.sample(available_russian, min(russian_count, len(available_russian))))

        intl_count = random.randint(1, 2)
        sites.extend(random.sample(INTERNATIONAL_SITES, min(intl_count, len(INTERNATIONAL_SITES))))

    elif stage >= 3:
        # Stage 3+: Yandex-heavy + Maps organization browsing
        yandex_count = random.randint(5, 8)
        sites.extend(random.sample(YANDEX_ECOSYSTEM, min(yandex_count, len(YANDEX_ECOSYSTEM))))

        # Yandex Maps — main + category pages
        maps_urls = [
            "https://yandex.ru/maps",
            "https://yandex.ru/maps/?ll=37.622504,55.753215&z=12",  # Moscow center
            "https://yandex.ru/maps/?ll=30.315868,59.939095&z=12",  # SPb
        ]
        sites.extend(random.sample(maps_urls, min(2, len(maps_urls))))

        russian_count = random.randint(3, 6)
        available_russian = [s for s in POPULAR_RUSSIAN_SITES if s not in sites]
        sites.extend(random.sample(available_russian, min(russian_count, len(available_russian))))

    # Add DB/domain URLs for diversity
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
        yandex_guaranteed = [s for s in sites if any(y in s for y in ["yandex", "ya.ru", "dzen.ru"])][:3]
        rest = [s for s in sites if s not in yandex_guaranteed]
        random.shuffle(rest)
        sites = yandex_guaranteed + rest[:count - len(yandex_guaranteed)]

    random.shuffle(sites)
    return sites


def _fast_sleep(min_t: float, max_t: float):
    """Sleep with fast_mode factor applied."""
    time.sleep(random.uniform(min_t * SPEED_FACTOR, max_t * SPEED_FACTOR))


def _smooth_scroll(driver, direction="down", distance=None):
    """Smooth human-like scroll using behavior:'smooth'."""
    if distance is None:
        distance = random.randint(200, 700)
    if direction == "up":
        distance = -distance

    steps = random.randint(2, 4) if FAST_MODE else random.randint(3, 6)
    step_size = distance // steps
    for i in range(steps):
        driver.execute_script(f"window.scrollBy({{top: {step_size}, behavior: 'smooth'}});")
        time.sleep(random.uniform(0.02, 0.06) if FAST_MODE else random.uniform(0.03, 0.1))
    _fast_sleep(0.3, 0.8)


def _human_read_page(driver, min_time=5, max_time=25):
    """Simulate a human reading a page: scroll, pause, look around."""
    read_time = random.uniform(min_time * SPEED_FACTOR, max_time * SPEED_FACTOR)
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
                _fast_sleep(0.5, 2.0)

            elif action == "scroll_up":
                _smooth_scroll(driver, "up", random.randint(100, 300))
                _fast_sleep(0.3, 1.0)

            elif action == "pause":
                # Just reading
                _fast_sleep(1.0, 4.0)

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
        _fast_sleep(2, 4)

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
            _fast_sleep(2, 4)
            _human_read_page(driver, min_time=5, max_time=12)
            logger.info(f"🔍 Yandex search (URL) completed: '{query}'")
            return True

        # Click on input
        ActionChains(driver).move_to_element(search_input).pause(
            random.uniform(0.2, 0.4)
        ).click().perform()
        _fast_sleep(0.3, 0.6)

        # Type query character by character
        for char in query:
            search_input.send_keys(char)
            time.sleep(random.uniform(0.03, 0.12) if FAST_MODE else random.uniform(0.05, 0.2))

        _fast_sleep(0.5, 1.5)

        # Submit
        search_input.send_keys(Keys.RETURN)
        _fast_sleep(2, 4)

        # Browse results: scroll and read
        _human_read_page(driver, min_time=5, max_time=12)

        # Sometimes click on a search result (30% chance)
        if random.random() < 0.3:
            try:
                results = driver.find_elements(By.CSS_SELECTOR, "a.OrganicTitle-Link, li.serp-item a.link, .organic__url")
                safe_results = [r for r in results if r.is_displayed()]
                if safe_results:
                    chosen = random.choice(safe_results[:5])
                    ActionChains(driver).move_to_element(chosen).pause(
                        random.uniform(0.2, 0.5)
                    ).click().perform()
                    _fast_sleep(2, 5)
                    _human_read_page(driver, min_time=3, max_time=10)
                    # Go back
                    driver.back()
                    _fast_sleep(1, 2)
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
        _fast_sleep(1, 3)

        # Dismiss consent if needed
        _try_dismiss_cookies(driver)
        _fast_sleep(0.3, 1.0)

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
            random.uniform(0.2, 0.4)
        ).click().perform()
        _fast_sleep(0.2, 0.5)

        for char in query:
            search_input.send_keys(char)
            time.sleep(random.uniform(0.03, 0.1) if FAST_MODE else random.uniform(0.05, 0.18))

        _fast_sleep(0.3, 1.0)
        search_input.send_keys(Keys.RETURN)
        _fast_sleep(2, 4)

        _human_read_page(driver, min_time=3, max_time=10)

        logger.info(f"🔍 Google search completed: '{query}'")
        return True

    except Exception as e:
        logger.warning(f"Error in Google search: {e}")
        return False


def _browse_yandex_maps(driver, query: str = None) -> bool:
    """Browse Yandex Maps: open maps, optionally search, scroll/zoom, click on organizations.
    
    This builds Yandex Maps cookies and browsing history so the profile
    doesn't appear as a first-time Maps visitor during the target visit.
    """
    try:
        # Go to Yandex Maps
        driver.get("https://yandex.ru/maps")
        _fast_sleep(3, 6)

        # Dismiss popups/banners
        _try_dismiss_cookies(driver)
        _fast_sleep(0.5, 1.5)

        # Interact with the map: zoom, pan
        try:
            # Zoom in/out with scroll
            map_el = driver.find_element(By.CSS_SELECTOR, ".ymaps3x0--map, [class*='map-container'], .map-container, ymaps, [data-testid='map']")
            if map_el and map_el.is_displayed():
                ActionChains(driver).move_to_element(map_el).perform()
                _fast_sleep(0.3, 1.0)
                # Scroll to zoom
                for _ in range(random.randint(2, 4)):
                    ActionChains(driver).scroll_by_amount(0, random.choice([-120, 120])).perform()
                    _fast_sleep(0.3, 1.0)
                # Click-drag to pan
                try:
                    ActionChains(driver).move_to_element_with_offset(
                        map_el, random.randint(-100, 100), random.randint(-50, 50)
                    ).click_and_hold().move_by_offset(
                        random.randint(-80, 80), random.randint(-40, 40)
                    ).release().perform()
                    _fast_sleep(0.5, 1.5)
                except:
                    pass
        except:
            logger.debug("Could not find map element for zoom/pan, continuing")

        # Search on maps if query provided
        if query:
            search_input = None
            for selector in [
                "input.input__control",
                "input[placeholder*='Поиск']",
                "input[placeholder*='Найд']",
                "input[aria-label*='Поиск']",
                ".search-form-view__input input",
                "input.suggest-input__input",
                "input[type='text']",
            ]:
                try:
                    elems = driver.find_elements(By.CSS_SELECTOR, selector)
                    for elem in elems:
                        if elem.is_displayed() and elem.size.get('height', 0) > 10:
                            search_input = elem
                            break
                    if search_input:
                        break
                except:
                    continue

            if search_input:
                ActionChains(driver).move_to_element(search_input).pause(
                    random.uniform(0.2, 0.5)
                ).click().perform()
                _fast_sleep(0.3, 0.6)

                # Clear existing text
                search_input.send_keys(Keys.CONTROL + "a")
                time.sleep(0.1)
                search_input.send_keys(Keys.DELETE)
                time.sleep(0.2)

                # Type query
                for char in query:
                    search_input.send_keys(char)
                    time.sleep(random.uniform(0.03, 0.1) if FAST_MODE else random.uniform(0.04, 0.15))
                _fast_sleep(0.5, 1.5)

                search_input.send_keys(Keys.RETURN)
                _fast_sleep(2, 5)

                # Browse search results — scroll the sidebar
                _human_read_page(driver, min_time=5, max_time=12)

                # Click on a random organization in results (50% chance)
                if random.random() < 0.5:
                    try:
                        org_selectors = [
                            "a.search-snippet-view__link-overlay",
                            ".search-snippet-view__body",
                            "[class*='SearchSnippet']",
                            ".search-list-view .card-title-view",
                            "li.search-snippet-view",
                            ".search-business-snippet-view",
                        ]
                        for sel in org_selectors:
                            orgs = driver.find_elements(By.CSS_SELECTOR, sel)
                            visible_orgs = [o for o in orgs if o.is_displayed()]
                            if visible_orgs:
                                chosen_org = random.choice(visible_orgs[:5])
                                ActionChains(driver).move_to_element(chosen_org).pause(
                                    random.uniform(0.3, 0.6)
                                ).click().perform()
                                _fast_sleep(2, 4)
                                # Read the organization card
                                _human_read_page(driver, min_time=3, max_time=10)
                                # Go back to results
                                driver.back()
                                _fast_sleep(1, 3)
                                break
                    except:
                        pass

                logger.info(f"🗺️ Yandex Maps search completed: '{query}'")
            else:
                # Fallback: direct URL search
                encoded = query.replace(' ', '+')
                driver.get(f"https://yandex.ru/maps/?text={encoded}")
                _fast_sleep(3, 6)
                _human_read_page(driver, min_time=5, max_time=12)
                logger.info(f"🗺️ Yandex Maps search (URL) completed: '{query}'")
        else:
            # Just browse the map without searching
            _human_read_page(driver, min_time=5, max_time=15)
            logger.info("🗺️ Yandex Maps browsing completed (no search)")

        return True

    except Exception as e:
        logger.warning(f"Error browsing Yandex Maps: {e}")
        return False


def _visit_site_with_actions(driver, url: str, site_index: int, total_sites: int) -> float:
    """Visit a site and perform realistic human actions. Returns time spent."""
    visit_start = time.time()

    try:
        # Try to dismiss cookie banners
        _try_dismiss_cookies(driver)
        _fast_sleep(0.3, 1.0)

        # Decide how long to stay based on site type
        if any(y in url for y in ["yandex", "ya.ru", "dzen.ru", "kinopoisk"]):
            min_time, max_time = 5, 15  # Yandex ecosystem
        elif any(s in url for s in ["vk.com", "ok.ru", "youtube", "pikabu", "habr"]):
            min_time, max_time = 4, 12  # Social/content sites
        elif any(s in url for s in ["ozon", "wildberries", "avito", "market", "dns-shop", "mvideo"]):
            min_time, max_time = 4, 15  # E-commerce
        else:
            min_time, max_time = 3, 10  # General sites

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
                        random.uniform(0.2, 0.5)
                    ).click().perform()
                    _fast_sleep(1, 3)
                    _human_read_page(driver, min_time=2, max_time=6)
                    # Go back
                    driver.back()
                    _fast_sleep(0.5, 1.5)
            except:
                pass

    except Exception as e:
        logger.debug(f"Minor error during site actions on {url}: {e}")

    return time.time() - visit_start


@shared_task(base=BaseTask, bind=True, max_retries=1, default_retry_delay=60, time_limit=900, soft_time_limit=840)
def warmup_profile_task(self, profile_id: int, duration_minutes: int = None, sites_list: List[str] = None):
    """
    Multi-session warmup: each call = one warmup session (stage).
    Profile needs 3+ sessions spread over 6+ hours to be fully warmed.
    
    Stage 1: Yandex search + general Russian sites (build cookies)
    Stage 2: More Yandex ecosystem + Yandex Maps exploration
    Stage 3: Yandex Maps search + organization browsing
    Stage 4+: Re-warmup / reinforcement

    The periodic_rewarmup scheduler calls this automatically for next stages.
    """
    browser_manager = None
    browser_id = None
    _profile_dir_for_cleanup = None  # Track profile dir for cleanup even if browser_id is None

    try:
        # Get profile from database and determine current stage
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
            
            current_stage = profile_obj.get_next_warmup_stage()
            is_rewarmup = profile_obj.warmup_completed  # re-warming already warmed profile

            profile_obj.status = "warming_up"
            db.commit()

        logger.info(f"🔥 Warmup profile {profile_id} — STAGE {current_stage} {'(re-warmup)' if is_rewarmup else ''}")

        # Build stage-appropriate site list
        if FAST_MODE:
            sites_count = random.randint(8, 12) if current_stage >= 2 else random.randint(8, 14)
        else:
            sites_count = random.randint(12, 18) if current_stage >= 2 else random.randint(15, 22)
        if not sites_list:
            sites_list = _build_warmup_site_list(profile_id, count=sites_count, stage=current_stage)

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

        if not proxy_data:
            error_msg = "🚫 Нет доступных прокси! Нагул без прокси запрещён."
            logger.error(error_msg)
            with get_db_session() as db:
                profile_obj = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
                if profile_obj:
                    profile_obj.status = "error"
                    db.commit()
            return {'status': 'error', 'error': error_msg, 'profile_id': profile_id}

        # Generate profile data for browser
        profile_generator = ProfileGenerator()
        
        # Detect if this is a mobile profile based on user agent
        ua_str = profile_user_agent or ''
        is_mobile = 'Mobile' in ua_str and 'Android' in ua_str
        
        profile_data = profile_generator.generate_profile(profile_name, is_mobile=is_mobile)
        profile_data.update({
            'user_agent': profile_user_agent or profile_data['user_agent'],
            'viewport': {
                'width': profile_viewport_width,
                'height': profile_viewport_height
            },
            'timezone': profile_timezone,
            'language': profile_language
        })
        
        if is_mobile:
            logger.info(f"📱 Mobile warmup profile: {profile_name}")

        # Track profile dir for cleanup even if Chrome fails to start
        from app.config import settings as _settings
        _profile_dir_for_cleanup = os.path.join(_settings.browser_user_data_dir, profile_data['name'])

        # Create browser session
        browser_id = browser_manager.create_browser_session(profile_data, proxy_data)
        driver = browser_manager.active_browsers.get(browser_id)
        if not driver:
            raise RuntimeError(f"Failed to get driver for session {browser_id}")

        logger.info(f"Created browser session {browser_id} for profile {profile_id} (stage {current_stage})")

        # === STAGE-BASED WARMUP ===
        start_time = time.time()
        sites_visited = 0
        successful_visits = 0
        total_time_spent = 0
        searches_done = 0
        maps_browsed = 0

        # --- Stage-specific pre-browsing ---
        if current_stage == 1:
            # Stage 1: Start with Yandex search to get cookies
            if random.random() < 0.9:
                query = random.choice(YANDEX_SEARCH_QUERIES)
                if _perform_yandex_search(driver, query):
                    searches_done += 1
                    total_time_spent += 15
                _fast_sleep(2, 5)

        elif current_stage == 2:
            # Stage 2: Yandex search + first Maps visit
            query = random.choice(YANDEX_SEARCH_QUERIES)
            if _perform_yandex_search(driver, query):
                searches_done += 1
                total_time_spent += 15
            _fast_sleep(2, 5)

            # Browse Yandex Maps without search (just explore)
            if _browse_yandex_maps(driver, query=None):
                maps_browsed += 1
                total_time_spent += 20
            _fast_sleep(2, 4)

        elif current_stage >= 3:
            # Stage 3+: Yandex search + Maps with organization search
            query = random.choice(YANDEX_SEARCH_QUERIES)
            if _perform_yandex_search(driver, query):
                searches_done += 1
                total_time_spent += 15
            _fast_sleep(2, 5)

            # Browse Yandex Maps WITH search query
            maps_query = random.choice(YANDEX_MAPS_SEARCH_QUERIES)
            if _browse_yandex_maps(driver, query=maps_query):
                maps_browsed += 1
                total_time_spent += 25
            _fast_sleep(2, 5)

            # Sometimes do a second maps search (40% chance)
            if random.random() < 0.4:
                maps_query2 = random.choice([q for q in YANDEX_MAPS_SEARCH_QUERIES if q != maps_query])
                if _browse_yandex_maps(driver, query=maps_query2):
                    maps_browsed += 1
                    total_time_spent += 20
                _fast_sleep(2, 4)

        # --- Visit sites with realistic browsing ---
        consecutive_failures = 0
        for i, site_url in enumerate(sites_list):
            try:
                if browser_manager.navigate_to_url(browser_id, site_url, timeout=20):
                    sites_visited += 1
                    consecutive_failures = 0

                    visit_time = _visit_site_with_actions(driver, site_url, i, len(sites_list))
                    total_time_spent += visit_time
                    successful_visits += 1

                    logger.info(f"✅ [{successful_visits}/{len(sites_list)}] {site_url} — {visit_time:.1f}s")

                    if random.random() < 0.1:
                        _fast_sleep(3, 8)
                    else:
                        _fast_sleep(1, 3)
                else:
                    sites_visited += 1
                    consecutive_failures += 1
                    logger.warning(f"⚠️ Failed to load {site_url}, skipping")
                    _fast_sleep(0.5, 1.5)

                    if consecutive_failures >= 3:
                        logger.warning(f"🛑 {consecutive_failures} consecutive failures — stopping warmup early")
                        break

            except Exception as site_error:
                logger.error(f"Error visiting {site_url}: {site_error}")
                consecutive_failures += 1
                time.sleep(1)
                if consecutive_failures >= 3:
                    logger.warning(f"🛑 {consecutive_failures} consecutive errors — stopping warmup early")
                    break
                continue

            # Mid-session Google search (once, 15% chance in fast mode)
            if i == len(sites_list) // 2 and random.random() < (0.1 if FAST_MODE else 0.25) and searches_done < 2:
                query = random.choice(GOOGLE_SEARCH_QUERIES)
                if _perform_google_search_warmup(driver, query):
                    searches_done += 1
                _fast_sleep(2, 4)

        # --- End-of-session Yandex search reinforcement (20% in fast, 35% normal) ---
        if random.random() < (0.2 if FAST_MODE else 0.35) and searches_done < 3:
            query = random.choice(YANDEX_SEARCH_QUERIES)
            if _perform_yandex_search(driver, query):
                searches_done += 1
            _fast_sleep(1, 3)

        # Calculate results
        actual_duration = time.time() - start_time
        success_rate = (successful_visits / max(sites_visited, 1) * 100)

        # Update profile in database — multi-session logic
        with get_db_session() as db:
            profile_obj = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
            if profile_obj:
                profile_obj.warmup_sessions_count = (profile_obj.warmup_sessions_count or 0) + 1
                profile_obj.warmup_time_spent = (profile_obj.warmup_time_spent or 0) + max(1, int(actual_duration / 60))
                profile_obj.last_used_at = datetime.utcnow()
                
                if not is_rewarmup:
                    # Track stage progression
                    profile_obj.warmup_stage = current_stage
                    
                    # Set first_warmup_at on first session
                    if not profile_obj.first_warmup_at:
                        profile_obj.first_warmup_at = datetime.utcnow()
                    
                    # Check if profile is fully warmed
                    if current_stage >= MIN_WARMUP_SESSIONS:
                        # Check time spread
                        hours_since_first = 0
                        if profile_obj.first_warmup_at:
                            hours_since_first = (datetime.utcnow() - profile_obj.first_warmup_at).total_seconds() / 3600
                        
                        if hours_since_first >= MIN_WARMUP_HOURS_SPREAD:
                            # Fully warmed!
                            profile_obj.warmup_completed = True
                            profile_obj.status = "warmed"
                            logger.info(
                                f"✅ Profile {profile_id} FULLY WARMED after {current_stage} sessions "
                                f"over {hours_since_first:.1f} hours"
                            )
                        else:
                            # Enough sessions but need more time spread — schedule retry
                            profile_obj.status = "created"
                            remaining_hours = MIN_WARMUP_HOURS_SPREAD - hours_since_first
                            retry_minutes = max(30, int(remaining_hours * 60))
                            logger.info(
                                f"⏳ Profile {profile_id} completed stage {current_stage} but only "
                                f"{hours_since_first:.1f}h since first warmup (need {MIN_WARMUP_HOURS_SPREAD}h). "
                                f"Will retry in {retry_minutes} min."
                            )
                            # Schedule retry after the required time passes
                            warmup_profile_task.apply_async(
                                args=[profile_id],
                                eta=datetime.utcnow() + timedelta(minutes=retry_minutes),
                                queue='warmup'
                            )
                    else:
                        # More sessions needed — schedule next stage with delay
                        profile_obj.status = "created"
                        next_delay_min = max(5, WARMUP_SESSION_INTERVAL_HOURS * 60)
                        logger.info(
                            f"📋 Profile {profile_id} completed stage {current_stage}/{MIN_WARMUP_SESSIONS}. "
                            f"Next session scheduled in {next_delay_min} min."
                        )
                        # Auto-schedule next stage
                        warmup_profile_task.apply_async(
                            args=[profile_id],
                            eta=datetime.utcnow() + timedelta(minutes=next_delay_min),
                            queue='warmup'
                        )
                else:
                    # Re-warmup — advance stage for Maps warmup catch-up
                    if profile_obj.warmup_stage < current_stage:
                        profile_obj.warmup_stage = current_stage
                        logger.info(
                            f"📈 Profile {profile_id} re-warmup advanced to stage {current_stage}"
                        )
                    profile_obj.status = "warmed"
                
                db.commit()

        result = {
            "status": "completed",
            "profile_id": profile_id,
            "stage": current_stage,
            "is_rewarmup": is_rewarmup,
            "duration_seconds": round(actual_duration, 1),
            "sites_visited": sites_visited,
            "successful_visits": successful_visits,
            "success_rate": round(success_rate, 1),
            "searches_performed": searches_done,
            "maps_browsed": maps_browsed,
            "total_time_spent": round(total_time_spent, 1),
            "average_time_per_site": round(total_time_spent / max(successful_visits, 1), 1)
        }

        logger.info(
            f"🔥 Warmup DONE profile {profile_id} stage {current_stage} in {actual_duration:.0f}s: "
            f"{successful_visits}/{sites_visited} sites, {searches_done} searches, "
            f"{maps_browsed} maps sessions, "
            f"avg {result['average_time_per_site']:.1f}s/site"
        )
        return result

    except SoftTimeLimitExceeded:
        logger.error(f"⏰ Soft time limit exceeded for warmup profile {profile_id}, cleaning up Chrome...")
        raise

    except Exception as e:
        logger.error(f"Error in warmup task for profile {profile_id}: {e}")

        try:
            with get_db_session() as db:
                profile_obj = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
                if profile_obj:
                    # On error, reset to previous state so scheduler retries
                    if profile_obj.warmup_completed:
                        profile_obj.status = "warmed"
                    else:
                        profile_obj.status = "created"
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
    Advanced warmup with custom strategy — delegates to warmup_profile_task.
    Kept for API compatibility.
    """
    # Just delegate to the main warmup task which handles stages
    return warmup_profile_task(profile_id,
                               sites_list=warmup_strategy.get('sites') if warmup_strategy else None)


@shared_task(base=BaseTask)
def periodic_rewarmup():
    """
    Multi-session warmup scheduler + profile freshness keeper.
    Runs every 2 hours via Celery Beat.
    
    1. Schedules NEXT warmup sessions for profiles in multi-session warmup pipeline
       (stage < MIN_WARMUP_SESSIONS, not yet fully warmed)
    2. Re-warms already warmed profiles that haven't been used in 24+ hours
    """
    try:
        now = datetime.utcnow()
        scheduled_next = 0
        scheduled_rewarm = 0
        profile_ids_next = []
        profile_ids_rewarm = []

        with get_db_session() as db:
            # === Part 1: Multi-session warmup pipeline ===
            # Find profiles that need their next warmup session:
            # - Not fully warmed yet
            # - Status is "created" (previous session completed, waiting for next)
            # - warmup_stage > 0 (at least 1 session done)
            # - Last used at least WARMUP_SESSION_INTERVAL_HOURS ago
            interval_threshold = now - timedelta(hours=WARMUP_SESSION_INTERVAL_HOURS)

            pipeline_profiles = db.query(BrowserProfile).filter(
                BrowserProfile.warmup_completed == False,
                BrowserProfile.is_active == True,
                BrowserProfile.status == "created",
                BrowserProfile.warmup_stage >= 0,  # include stage 0 (never warmed) too
                BrowserProfile.warmup_stage < MIN_WARMUP_SESSIONS + 1,  # not done yet
                (BrowserProfile.last_used_at < interval_threshold) | (BrowserProfile.last_used_at.is_(None))
            ).order_by(BrowserProfile.warmup_stage.asc(), BrowserProfile.last_used_at.asc().nullsfirst()).limit(10).all()

            if pipeline_profiles:
                profile_ids_next = [p.id for p in pipeline_profiles]
                logger.info(
                    f"📋 Found {len(pipeline_profiles)} profiles needing next warmup session: "
                    f"{[(p.id, f'stage {p.warmup_stage}') for p in pipeline_profiles[:5]]}..."
                )

            # === Part 2: Re-warmup for already warmed profiles ===
            # Prioritize profiles that haven't been through Maps warmup stages (stage < 3)
            stale_threshold = now - timedelta(hours=4)  # more aggressive: 4h instead of 24h for catch-up
            stale_profiles = db.query(BrowserProfile).filter(
                BrowserProfile.warmup_completed == True,
                BrowserProfile.is_active == True,
                BrowserProfile.status.in_(["warmed", "created"]),
                (BrowserProfile.last_used_at < stale_threshold) | (BrowserProfile.last_used_at.is_(None))
            ).order_by(
                BrowserProfile.warmup_stage.asc(),  # low-stage profiles first (need Maps warmup)
                BrowserProfile.last_used_at.asc().nullsfirst()
            ).limit(5).all()

            if stale_profiles:
                profile_ids_rewarm = [p.id for p in stale_profiles]

        # Schedule pipeline warmup tasks with staggered delays
        for i, pid in enumerate(profile_ids_next):
            delay_seconds = i * random.randint(5, 15)
            eta = now + timedelta(seconds=delay_seconds)
            warmup_profile_task.apply_async(args=[pid], eta=eta, queue='warmup')
            scheduled_next += 1

        # Schedule re-warmup tasks
        for i, pid in enumerate(profile_ids_rewarm):
            delay_seconds = (len(profile_ids_next) + i) * random.randint(5, 20)
            eta = now + timedelta(seconds=delay_seconds)
            warmup_profile_task.apply_async(args=[pid], eta=eta, queue='warmup')
            scheduled_rewarm += 1

        if scheduled_next > 0:
            logger.info(f"🔄 Scheduled {scheduled_next} next-stage warmup sessions: {profile_ids_next}")
        if scheduled_rewarm > 0:
            logger.info(f"🔄 Scheduled {scheduled_rewarm} re-warmup sessions: {profile_ids_rewarm}")
        if scheduled_next == 0 and scheduled_rewarm == 0:
            logger.info("📋 No warmup sessions needed right now")

        return {
            "pipeline_scheduled": scheduled_next,
            "pipeline_profile_ids": profile_ids_next,
            "rewarm_scheduled": scheduled_rewarm,
            "rewarm_profile_ids": profile_ids_rewarm
        }

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
def auto_schedule_initial_warmup():
    """
    Auto-schedule initial warmup for profiles that haven't started yet.
    Runs every 5 minutes via Celery Beat.
    
    Picks up profiles with warmup_stage=0 (never warmed) and schedules them
    in batches. Also resets stuck 'warming_up' profiles back to 'created'
    so they can be picked up again.
    """
    try:
        now = datetime.utcnow()
        scheduled = 0
        reset_count = 0

        with get_db_session() as db:
            # First, reset profiles stuck in 'warming_up' for >10 min
            stuck_threshold = now - timedelta(minutes=10)
            stuck = db.query(BrowserProfile).filter(
                BrowserProfile.status == "warming_up",
                BrowserProfile.updated_at < stuck_threshold
            ).all()
            for p in stuck:
                p.status = "created" if not p.warmup_completed else "warmed"
                p.updated_at = now
                reset_count += 1
            if reset_count:
                db.commit()
                logger.info(f"🔧 Reset {reset_count} stuck warming_up profiles to 'created'")

            # Count currently active warmup tasks
            active_warming = db.query(BrowserProfile).filter(
                BrowserProfile.status == "warming_up"
            ).count()

            # Allow up to 5 concurrent warmup tasks (matches actual worker concurrency)
            MAX_WARMUP_CONCURRENT = 5
            slots = max(0, MAX_WARMUP_CONCURRENT - active_warming)

            if slots <= 0:
                logger.info(f"⏭️ Warmup slots full ({active_warming} active), skipping")
                return {"scheduled": 0, "reset": reset_count, "active": active_warming}

            # Find profiles needing warmup:
            # 1) stage 0 (never started) OR
            # 2) stage > 0 but warmup not completed (need continuation)
            # All must be status='created' and active
            interval_threshold = now - timedelta(hours=WARMUP_SESSION_INTERVAL_HOURS)
            new_profiles = db.query(BrowserProfile).filter(
                BrowserProfile.warmup_completed == False,
                BrowserProfile.is_active == True,
                BrowserProfile.status == "created",
                BrowserProfile.warmup_stage < MIN_WARMUP_SESSIONS + 1,
                (BrowserProfile.last_used_at < interval_threshold) | (BrowserProfile.last_used_at.is_(None))
            ).order_by(
                BrowserProfile.warmup_stage.asc(),  # prioritize least warmed
                BrowserProfile.id.asc()  # oldest first
            ).limit(slots).all()

            profile_ids = [p.id for p in new_profiles]

        if not profile_ids:
            logger.info(f"📋 No new profiles need initial warmup (reset={reset_count})")
            return {"scheduled": 0, "reset": reset_count}

        # Schedule warmup tasks with minimal stagger
        for i, pid in enumerate(profile_ids):
            delay_seconds = i * random.randint(2, 5)
            eta = now + timedelta(seconds=delay_seconds)
            warmup_profile_task.apply_async(args=[pid], eta=eta, queue='warmup')
            scheduled += 1

        logger.info(
            f"🚀 Auto-scheduled {scheduled} initial warmup tasks: "
            f"profiles {profile_ids[:5]}{'...' if len(profile_ids) > 5 else ''} "
            f"(reset={reset_count}, active={active_warming})"
        )

        return {
            "scheduled": scheduled,
            "profile_ids": profile_ids,
            "reset": reset_count,
            "active_warming": active_warming
        }

    except Exception as e:
        logger.error(f"Error in auto_schedule_initial_warmup: {e}")
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
        stuck_threshold = timedelta(minutes=10)

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
            # For yandex_visit tasks: 7 min threshold (hard limit is 210s)
            # For all other tasks: 40 min threshold
            stalled_yandex = db.query(Task).filter(
                Task.status == "in_progress",
                Task.task_type == "yandex_visit",
                Task.started_at.isnot(None),
                Task.started_at < (now - timedelta(minutes=7))
            ).all()
            
            for t in stalled_yandex:
                t.status = "failed"
                t.error_message = t.error_message or "Автоматически отменена: задача зависла (>7 мин, OOM-kill)"
                t.completed_at = now
                fixed += 1
                logger.warning(f"🔧 Auto-cancelled stalled yandex task {t.id}: {t.name}")
            
            stalled_threshold = timedelta(minutes=40)
            stalled_tasks = db.query(Task).filter(
                Task.status == "in_progress",
                Task.task_type != "yandex_visit",
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