"""
Profile warmup tasks for training browser profiles.
Realistic browsing sessions that build history, cookies, and behavioral patterns.
"""
import os
import time
import random
import signal
import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from core.playwright_driver import (
    By, Keys, EC, expected_conditions,
    PlaywrightActionChains as ActionChains,
    PlaywrightWait as WebDriverWait,
    TimeoutException, WebDriverException, StaleElementReferenceException,
)

from app.database import get_db_session, get_setting
from app.models import BrowserProfile, Task
from app.config import settings
from sqlalchemy import func
from core import BrowserManager, ProxyManager, ProfileGenerator
from core.domain_manager import domain_manager
from core.warmup_url_manager import get_warmup_urls
from .celery_app import BaseTask

import redis as _redis

def _get_warmup_redis():
    """Get Redis connection for warmup completion tracking."""
    return _redis.Redis(
        host=os.environ.get('YANDEX_BOT_REDIS_HOST', 'redis'),
        port=int(os.environ.get('YANDEX_BOT_REDIS_PORT', 6379)),
        db=0, decode_responses=True
    )

# Fast mode: reduce all delays by this factor for higher throughput
FAST_MODE = getattr(settings, 'fast_mode', False)

logger = logging.getLogger(__name__)


def recover_stuck_warming_profiles():
    """Reset profiles stuck in 'warming_up' after container restart.
    Called at module load time so orphaned profiles get re-queued."""
    try:
        with get_db_session() as db:
            stuck = db.query(BrowserProfile).filter(BrowserProfile.status == "warming_up").all()
            if stuck:
                for p in stuck:
                    p.status = "created" if not p.warmup_completed else "warmed"
                db.commit()
                logger.info(f"♻️ Recovered {len(stuck)} profiles stuck in 'warming_up' after restart")
    except Exception as e:
        logger.warning(f"Failed to recover stuck profiles: {e}")


recover_stuck_warming_profiles()
SPEED_FACTOR = 0.5 if FAST_MODE else 1.0  # 50% of normal time in fast mode

# === Warmup site pools ===

# Yandex ecosystem — DISABLED (captcha not handled during warmup)
YANDEX_ECOSYSTEM = []

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

# Yandex search/maps queries — DISABLED (captcha not handled during warmup)
YANDEX_SEARCH_QUERIES = []
YANDEX_MAPS_SEARCH_QUERIES = []

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
MIN_WARMUP_HOURS_SPREAD = 1
# Hours between warmup sessions
WARMUP_SESSION_INTERVAL_HOURS = 0.25
# Chunked warmup: max sites per browser session chunk
WARMUP_CHUNK_MAX_SITES = 10
# Minimum successful site visits required to count a warmup session
MIN_WARMUP_VISITS_PER_SESSION = 10

# === 10K external sites pool ===
_EXTERNAL_SITES_POOL = []

def _load_external_sites():
    """Load 10K sites from data/warmup_sites_10k.txt (lazy, once)."""
    global _EXTERNAL_SITES_POOL
    if _EXTERNAL_SITES_POOL:
        return _EXTERNAL_SITES_POOL
    sites_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "warmup_sites_10k.txt")
    try:
        with open(sites_file, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        _EXTERNAL_SITES_POOL = [f"https://{d}" if not d.startswith("http") else d for d in lines]
        logger.info(f"📂 Loaded {len(_EXTERNAL_SITES_POOL)} external warmup sites from {sites_file}")
    except FileNotFoundError:
        logger.warning(f"External sites file not found: {sites_file}")
    except Exception as e:
        logger.error(f"Failed to load external sites: {e}")
    return _EXTERNAL_SITES_POOL


def _get_unique_external_sites(profile_id: int, count: int, stage: int = 1, exclude: list = None) -> List[str]:
    """Pick a unique subset of external sites for a profile.
    
    Uses profile_id + stage as seed so each profile gets a deterministic
    but different selection. Different stages give different sites.
    """
    pool = _load_external_sites()
    if not pool:
        return []
    exclude_set = set(exclude or [])
    available = [s for s in pool if s not in exclude_set]
    if not available:
        return []
    rng = random.Random(profile_id * 1000 + stage)
    return rng.sample(available, min(count, len(available)))


@shared_task(bind=True, max_retries=2, default_retry_delay=30, time_limit=600, soft_time_limit=540)
def generate_warmup_sites_task(self, profile_ids: List[int]):
    """
    Background task: generate AI warmup sites (50 URLs + 20 queries)
    for profiles that have personas but no warmup_sites yet.
    Called after bulk profile creation.
    """
    from core.ai_persona_generator import generate_warmup_sites
    from sqlalchemy.orm.attributes import flag_modified

    generated = 0
    errors = 0

    for pid in profile_ids:
        try:
            with get_db_session() as db:
                profile = db.query(BrowserProfile).filter(BrowserProfile.id == pid).first()
                if not profile:
                    continue

                persona = profile.persona_data
                if not isinstance(persona, dict) or not persona.get("name"):
                    continue

                # Skip if already has warmup sites
                existing = persona.get("warmup_sites", [])
                if isinstance(existing, list) and len(existing) >= 20:
                    continue

                ws_data = generate_warmup_sites(persona)
                persona["warmup_sites"] = ws_data.get("warmup_sites", [])
                persona["extra_search_queries"] = ws_data.get("extra_search_queries", [])
                profile.persona_data = persona
                flag_modified(profile, "persona_data")
                db.commit()

                generated += 1
                logger.info(f"🌐 Generated {len(persona['warmup_sites'])} warmup sites for profile {pid}")

        except Exception as e:
            errors += 1
            logger.error(f"Error generating warmup sites for profile {pid}: {e}")

    logger.info(f"✅ Warmup sites generation complete: {generated} generated, {errors} errors (out of {len(profile_ids)} profiles)")
    return {"generated": generated, "errors": errors, "total": len(profile_ids)}


def _build_warmup_site_list(profile_id: int, count: int = 50, stage: int = 1, persona_data: dict = None) -> List[str]:
    """Build a diverse site list for warmup.
    
    Primary source: 10K external domains pool (unique per profile+stage).
    Supplemented with Russian/International hardcoded lists and DB URLs.
    """
    sites = []

    # Russian sites (5-10)
    russian_count = random.randint(5, 10)
    available_russian = [s for s in POPULAR_RUSSIAN_SITES if s not in sites]
    sites.extend(random.sample(available_russian, min(russian_count, len(available_russian))))

    # International sites (2-4)
    intl_count = random.randint(2, 4)
    sites.extend(random.sample(INTERNATIONAL_SITES, min(intl_count, len(INTERNATIONAL_SITES))))

    # DB/domain URLs for diversity
    try:
        db_urls = get_warmup_urls(count=5, profile_id=profile_id, strategy="diverse")
        if db_urls:
            for url in db_urls:
                if url not in sites:
                    sites.append(url)
    except:
        pass

    # Main source: unique external sites from 10K pool (fill up to count)
    ext_count = max(count - len(sites), 30)
    ext_sites = _get_unique_external_sites(profile_id, ext_count, stage=stage, exclude=sites)
    sites.extend(ext_sites)

    # Trim to requested count, shuffle
    if len(sites) > count:
        random.shuffle(sites)
        sites = sites[:count]

    random.shuffle(sites)
    return sites


def _fast_sleep(min_t: float, max_t: float):
    """Sleep with fast_mode factor applied."""
    time.sleep(random.uniform(min_t * SPEED_FACTOR, max_t * SPEED_FACTOR))


def _safe_back(driver):
    """Go back and immediately stop any pending navigation to prevent evaluate() hangs."""
    try:
        driver.back()
    except Exception:
        pass
    try:
        driver.execute_cdp_cmd("Page.stopLoading")
    except Exception:
        pass


def _safe_get(driver, url: str) -> bool:
    """Navigate to url, stop pending navigation on timeout. Returns True on success."""
    try:
        driver.get(url)
        return True
    except Exception:
        try:
            driver.execute_cdp_cmd("Page.stopLoading")
        except Exception:
            pass
        return False


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
    """Simulate a human reading a page: scroll, pause, look around.
    
    Each Playwright call is wrapped in try/except to prevent a single broken
    page from hanging the entire function. If execute_script hangs for >10s
    (the default JS timeout in playwright_driver.py), it will throw and we
    gracefully move on.
    """
    read_time = random.uniform(min_time * SPEED_FACTOR, max_time * SPEED_FACTOR)
    # Hard-cap read time to avoid exceeding caller's budget
    read_time = min(read_time, 30)
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
        except Exception as e:
            # Any Playwright call can hang/timeout — break out rather than
            # retrying on a broken page which will keep failing
            logger.debug(f"_human_read_page action {action} failed: {e}")
            break


def _try_dismiss_cookies(driver):
    """Try to accept/dismiss cookie consent banners.
    Hard-capped at 5s to prevent accumulating delays across 10+ calls per chunk."""
    deadline = time.time() + 5
    try:
        selectors = [
            "button[class*='cookie']", "button[class*='consent']",
            "button[class*='accept']", "a[class*='cookie']",
            "[data-testid*='cookie'] button", ".cookie-banner button",
            "#cookie-accept", ".js-cookie-accept",
            "button[class*='agree']", ".gdpr-accept",
        ]
        for sel in selectors:
            if time.time() > deadline:
                return False
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


def _read_dzen_articles(driver) -> bool:
    """Open Dzen.ru, browse feed, and read 1-2 articles in depth."""
    try:
        if not _safe_get(driver, "https://dzen.ru"): return False
        _fast_sleep(3, 5)
        _try_dismiss_cookies(driver)
        _fast_sleep(1, 2)

        # Scroll the feed to load articles
        _human_read_page(driver, min_time=5, max_time=10)

        # Find article links in the feed
        article_selectors = [
            "a[href*='/a/']",
            "a[data-testid='publication-title-link']",
            ".feed__item a[href*='dzen.ru']",
            ".card-image-one-column a",
            "article a",
        ]
        articles = []
        for sel in article_selectors:
            try:
                found = driver.find_elements(By.CSS_SELECTOR, sel)
                articles.extend([a for a in found if a.is_displayed() and a.size.get('height', 0) > 20])
            except:
                continue

        if not articles:
            logger.debug("No Dzen articles found, reading feed only")
            _human_read_page(driver, min_time=5, max_time=15)
            return True

        # Read 1-2 articles
        articles_to_read = random.sample(articles[:15], min(random.randint(1, 2), len(articles[:15])))
        for article in articles_to_read:
            try:
                ActionChains(driver).move_to_element(article).pause(
                    random.uniform(0.3, 0.7)
                ).click().perform()
                _fast_sleep(2, 4)

                # Read the article thoroughly
                _human_read_page(driver, min_time=8, max_time=20)

                # Go back to feed
                _safe_back(driver)
                _fast_sleep(1, 3)
                # Scroll a bit more in the feed
                _smooth_scroll(driver, "down", random.randint(300, 600))
                _fast_sleep(1, 2)
            except:
                _safe_back(driver)
                continue

        logger.info("📰 Dzen article reading completed")
        return True

    except Exception as e:
        logger.warning(f"Error reading Dzen articles: {e}")
        return False


def _watch_youtube_video(driver) -> bool:
    """Open YouTube, search for a topic, and watch a video briefly.
    Hard-capped at 60s to prevent hanging the chunk."""
    yt_start = time.time()
    YT_MAX_SECONDS = 60
    try:
        topics = [
            "обзор автомобиля", "рецепт ужина", "тренировка дома",
            "путешествие россия", "ремонт квартиры", "лайфхаки кухня",
            "новости технологий", "фильмы 2025 обзор", "музыка для работы",
        ]
        query = random.choice(topics)

        if not _safe_get(driver, f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}"):
            return False
        _fast_sleep(3, 5)
        _try_dismiss_cookies(driver)
        _fast_sleep(1, 2)

        # Find video thumbnails in search results
        video_links = []
        for sel in ["a#video-title", "a.ytd-video-renderer", "ytd-video-renderer a#thumbnail"]:
            try:
                found = driver.find_elements(By.CSS_SELECTOR, sel)
                video_links.extend([v for v in found if v.is_displayed()])
            except:
                continue

        if video_links:
            # Click a random video from top 8
            chosen = random.choice(video_links[:8])
            ActionChains(driver).move_to_element(chosen).pause(
                random.uniform(0.3, 0.6)
            ).click().perform()
            _fast_sleep(3, 5)

            # "Watch" for 15-30 seconds (scroll comments, pause)
            # Cap to remaining YT budget
            remaining = YT_MAX_SECONDS - (time.time() - yt_start)
            watch_time = min(random.uniform(15, 30) * SPEED_FACTOR, max(5, remaining - 3))
            end_time = time.time() + watch_time
            while time.time() < end_time:
                action = random.choice(["pause", "scroll", "pause"])
                if action == "scroll":
                    _smooth_scroll(driver, "down", random.randint(100, 300))
                _fast_sleep(2, 6)

            logger.info(f"📺 YouTube video watched: '{query}' ({watch_time:.0f}s)")
        else:
            # Just browse search results
            _human_read_page(driver, min_time=5, max_time=10)
            logger.info(f"📺 YouTube browsed: '{query}'")

        return True

    except Exception as e:
        logger.warning(f"Error watching YouTube: {e}")
        return False


def _deep_yandex_interaction(driver) -> bool:
    """Interact with Yandex services: weather details, translate, images search."""
    try:
        service = random.choice(["weather", "translate", "images", "news"])

        if service == "weather":
            if not _safe_get(driver, "https://pogoda.yandex.ru"): return False
            _fast_sleep(2, 4)
            _try_dismiss_cookies(driver)
            # Read the forecast
            _human_read_page(driver, min_time=5, max_time=12)
            # Try clicking on a specific day
            try:
                day_links = driver.find_elements(By.CSS_SELECTOR, "a[href*='details'], .forecast-briefly__day, .link_theme_normal")
                visible = [d for d in day_links if d.is_displayed() and d.size.get('height', 0) > 10]
                if visible:
                    ActionChains(driver).move_to_element(random.choice(visible[:5])).pause(0.3).click().perform()
                    _fast_sleep(2, 3)
                    _human_read_page(driver, min_time=3, max_time=8)
            except:
                pass
            logger.info("🌤️ Yandex Weather browsed in depth")

        elif service == "translate":
            if not _safe_get(driver, "https://translate.yandex.ru"): return False
            _fast_sleep(2, 4)
            _try_dismiss_cookies(driver)
            # Type a phrase to translate
            phrases = ["привет как дела", "хороший ресторан рядом", "сколько стоит доставка",
                       "расписание электричек", "прогноз погоды на неделю"]
            phrase = random.choice(phrases)
            try:
                text_input = None
                for sel in ["textarea#fakeArea", "textarea.textinput__control", "div[contenteditable]", "textarea"]:
                    try:
                        elems = driver.find_elements(By.CSS_SELECTOR, sel)
                        for elem in elems:
                            if elem.is_displayed():
                                text_input = elem
                                break
                        if text_input:
                            break
                    except:
                        continue
                if text_input:
                    text_input.click()
                    _fast_sleep(0.3, 0.6)
                    for char in phrase:
                        text_input.send_keys(char)
                        time.sleep(random.uniform(0.04, 0.12) * SPEED_FACTOR)
                    _fast_sleep(2, 4)  # Wait for translation
                    _human_read_page(driver, min_time=3, max_time=6)
            except:
                pass
            logger.info("🌐 Yandex Translate used")

        elif service == "images":
            queries = ["красивые места России", "рецепты тортов", "интерьер квартиры",
                       "котики", "природа Байкал"]
            query = random.choice(queries)
            if not _safe_get(driver, f"https://yandex.ru/images/search?text={query.replace(' ', '+')}"):
                return False
            _fast_sleep(3, 5)
            _human_read_page(driver, min_time=5, max_time=12)
            # Click on an image (30% chance)
            if random.random() < 0.3:
                try:
                    imgs = driver.find_elements(By.CSS_SELECTOR, ".serp-item__link, .serp-item a, img.serp-item__thumb")
                    visible_imgs = [im for im in imgs if im.is_displayed()]
                    if visible_imgs:
                        ActionChains(driver).move_to_element(random.choice(visible_imgs[:10])).pause(0.3).click().perform()
                        _fast_sleep(2, 4)
                        _human_read_page(driver, min_time=3, max_time=6)
                        _safe_back(driver)
                        _fast_sleep(1, 2)
                except:
                    pass
            logger.info(f"🖼️ Yandex Images browsed: '{query}'")

        elif service == "news":
            if not _safe_get(driver, "https://dzen.ru/news"): return False
            _fast_sleep(2, 4)
            _try_dismiss_cookies(driver)
            _human_read_page(driver, min_time=5, max_time=12)
            # Click on a news article (40% chance)
            if random.random() < 0.4:
                try:
                    news = driver.find_elements(By.CSS_SELECTOR, "a[href*='story'], a.mg-card__link, .news-item a")
                    visible_news = [n for n in news if n.is_displayed()]
                    if visible_news:
                        ActionChains(driver).move_to_element(random.choice(visible_news[:8])).pause(0.3).click().perform()
                        _fast_sleep(2, 4)
                        _human_read_page(driver, min_time=5, max_time=12)
                        _safe_back(driver)
                        _fast_sleep(1, 2)
                except:
                    pass
            logger.info("📰 Yandex News browsed")

        return True

    except Exception as e:
        logger.warning(f"Error in deep Yandex interaction: {e}")
        return False


def _inject_yandex_trust_markers(driver) -> bool:
    """Set localStorage/cookie markers that Yandex uses to trust returning users."""
    try:
        script = """
        try {
            // Yandex uses these localStorage keys to track returning users
            const now = Date.now();
            const pastVisit = now - Math.floor(Math.random() * 86400000 * 7); // 1-7 days ago
            
            localStorage.setItem('yandex_login_hint', '');
            localStorage.setItem('settings_lang', 'ru');
            localStorage.setItem('yandex_gid', '213');  // Moscow geoid
            
            // Simulate previous Yandex visits timestamp
            if (!localStorage.getItem('_ym_isad')) {
                localStorage.setItem('_ym_isad', '1');
            }
            
            // Maps-specific trust
            localStorage.setItem('maps_last_position', JSON.stringify({
                ll: [37.622504 + (Math.random()-0.5)*0.1, 55.753215 + (Math.random()-0.5)*0.05],
                z: Math.floor(10 + Math.random() * 5)
            }));
            
            return true;
        } catch(e) { return false; }
        """
        result = driver.execute_script(script)
        if result:
            logger.debug("✅ Yandex trust markers injected into localStorage")
        return bool(result)
    except Exception as e:
        logger.debug(f"Could not inject trust markers: {e}")
        return False


# === Yandex Market queries for warmup ===
YANDEX_MARKET_QUERIES = [
    "наушники беспроводные", "чехол для телефона", "кроссовки мужские",
    "робот пылесос", "кофемашина", "ноутбук для учёбы",
    "смартфон до 30000", "электрическая зубная щётка", "рюкзак городской",
    "блендер", "фитнес браслет", "книга бестселлер",
    "настольная лампа", "сковорода с антипригарным покрытием",
    "зимняя куртка мужская", "детские игрушки",
]

# === Kinopoisk / movie queries ===
KINOPOISK_QUERIES = [
    "лучшие фильмы 2025", "комедии русские", "сериалы новинки",
    "триллеры топ", "фантастика фильмы", "драмы оскар",
    "мультфильмы для детей", "детективы сериалы", "исторические фильмы",
    "документальные фильмы", "ужасы новинки", "аниме популярные",
]

# === Yandex Music queries ===
YANDEX_MUSIC_QUERIES = [
    "русский рок", "поп музыка 2025", "классическая музыка",
    "рэп русский", "инди музыка", "электронная музыка",
    "джаз плейлист", "музыка для тренировки", "спокойная музыка для сна",
    "хиты 90-х", "новинки музыки", "кавказская музыка",
]


def _browse_yandex_market(driver) -> bool:
    """Browse Yandex Market: search products, view cards, read reviews, use filters."""
    try:
        query = random.choice(YANDEX_MARKET_QUERIES)
        if not _safe_get(driver, f"https://market.yandex.ru/search?text={query.replace(' ', '+')}"):
            return False
        _fast_sleep(3, 6)
        _try_dismiss_cookies(driver)
        _fast_sleep(1, 2)

        # Scroll through product listings
        _human_read_page(driver, min_time=5, max_time=12)

        # Try to apply a filter (30% chance) — price sort or rating
        if random.random() < 0.3:
            try:
                filter_selectors = [
                    "button[data-autotest-id*='dprice']",
                    "[data-autotest-id='sort'] button",
                    "button[data-zone-name='sort']",
                    "a[href*='how=dprice']",
                    "a[href*='how=aprice']",
                    ".n-filter-sorter button",
                ]
                for sel in filter_selectors:
                    btns = driver.find_elements(By.CSS_SELECTOR, sel)
                    visible = [b for b in btns if b.is_displayed() and b.size.get('height', 0) > 10]
                    if visible:
                        ActionChains(driver).move_to_element(random.choice(visible[:3])).pause(
                            random.uniform(0.3, 0.6)
                        ).click().perform()
                        _fast_sleep(2, 4)
                        _human_read_page(driver, min_time=3, max_time=6)
                        break
            except:
                pass

        # Click on a product card (70% chance)
        if random.random() < 0.7:
            try:
                product_selectors = [
                    "a[data-autotest-id='product-link']",
                    "article a[href*='/product--']",
                    "a[href*='/product--']",
                    ".n-snippet-card2__title a",
                    "[data-zone-name='snippetList'] a[href*='product']",
                    "a[data-baobab-name='title']",
                ]
                products = []
                for sel in product_selectors:
                    found = driver.find_elements(By.CSS_SELECTOR, sel)
                    products.extend([p for p in found if p.is_displayed() and p.size.get('height', 0) > 10])
                    if len(products) >= 5:
                        break

                if products:
                    chosen = random.choice(products[:8])
                    ActionChains(driver).move_to_element(chosen).pause(
                        random.uniform(0.3, 0.6)
                    ).click().perform()
                    _fast_sleep(3, 5)

                    # Read the product page: scroll through specs, photos
                    _human_read_page(driver, min_time=8, max_time=18)

                    # Try scrolling to reviews section (50% chance)
                    if random.random() < 0.5:
                        try:
                            review_selectors = [
                                "a[href*='reviews']",
                                "[data-autotest-id='product-review']",
                                "span:has-text('Отзывы')",
                                "a[data-zone-name='reviews']",
                                "[id*='review']",
                            ]
                            for sel in review_selectors:
                                elems = driver.find_elements(By.CSS_SELECTOR, sel)
                                visible = [e for e in elems if e.is_displayed()]
                                if visible:
                                    ActionChains(driver).move_to_element(visible[0]).perform()
                                    _fast_sleep(0.5, 1)
                                    visible[0].click()
                                    _fast_sleep(2, 4)
                                    _human_read_page(driver, min_time=5, max_time=12)
                                    break
                        except:
                            pass

                    _safe_back(driver)
                    _fast_sleep(1, 3)
            except:
                pass

        logger.info(f"🛒 Yandex Market browsed: '{query}'")
        return True

    except Exception as e:
        logger.warning(f"Error browsing Yandex Market: {e}")
        return False


def _browse_kinopoisk(driver) -> bool:
    """Browse Kinopoisk: explore movies, read ratings and reviews."""
    try:
        action = random.choice(["main", "search", "top"])

        if action == "main":
            # Browse main page (hd.kinopoisk.ru avoids CSP issues)
            if not _safe_get(driver, "https://hd.kinopoisk.ru"): return False
            _fast_sleep(3, 5)
            _try_dismiss_cookies(driver)
            _human_read_page(driver, min_time=5, max_time=12)

        elif action == "search":
            # Search for something
            query = random.choice(KINOPOISK_QUERIES)
            if not _safe_get(driver, f"https://www.kinopoisk.ru/s/type/all/find/{query.replace(' ', '+')}/"): return False
            _fast_sleep(3, 5)
            _try_dismiss_cookies(driver)
            _human_read_page(driver, min_time=5, max_time=10)

        else:
            # Browse top-250
            if not _safe_get(driver, "https://www.kinopoisk.ru/lists/movies/top250/"): return False
            _fast_sleep(3, 5)
            _try_dismiss_cookies(driver)
            _human_read_page(driver, min_time=5, max_time=12)

        # Click on a movie/series card (60% chance)
        if random.random() < 0.6:
            try:
                movie_selectors = [
                    "a[href*='/film/']",
                    "a[href*='/series/']",
                    "a[data-tid='film-link']",
                    ".selection-film-item-meta a",
                    ".styles_root__m0wje a",
                    "a.base-movie-main-info_link",
                ]
                movies = []
                for sel in movie_selectors:
                    found = driver.find_elements(By.CSS_SELECTOR, sel)
                    movies.extend([m for m in found if m.is_displayed() and m.size.get('height', 0) > 10])
                    if len(movies) >= 8:
                        break

                if movies:
                    chosen = random.choice(movies[:10])
                    ActionChains(driver).move_to_element(chosen).pause(
                        random.uniform(0.3, 0.6)
                    ).click().perform()
                    _fast_sleep(3, 5)

                    # Read the movie page: plot, cast, rating
                    _human_read_page(driver, min_time=8, max_time=20)

                    # Scroll to reviews (40% chance)
                    if random.random() < 0.4:
                        try:
                            _smooth_scroll(driver, "down", random.randint(800, 1500))
                            _fast_sleep(1, 2)
                            _human_read_page(driver, min_time=3, max_time=8)
                        except:
                            pass

                    _safe_back(driver)
                    _fast_sleep(1, 2)
            except:
                pass

        logger.info("🎬 Kinopoisk browsed")
        return True

    except Exception as e:
        logger.warning(f"Error browsing Kinopoisk: {e}")
        return False


def _browse_yandex_music(driver) -> bool:
    """Browse Yandex Music: search artists, explore playlists, play previews."""
    try:
        action = random.choice(["main", "search", "chart"])

        if action == "main":
            if not _safe_get(driver, "https://music.yandex.ru"): return False
            _fast_sleep(3, 5)
            _try_dismiss_cookies(driver)
            _human_read_page(driver, min_time=5, max_time=12)

        elif action == "search":
            query = random.choice(YANDEX_MUSIC_QUERIES)
            if not _safe_get(driver, f"https://music.yandex.ru/search?text={query.replace(' ', '+')}"):
                return False
            _fast_sleep(3, 5)
            _try_dismiss_cookies(driver)
            _human_read_page(driver, min_time=5, max_time=10)

        else:
            # Browse chart
            if not _safe_get(driver, "https://music.yandex.ru/chart"): return False
            _fast_sleep(3, 5)
            _try_dismiss_cookies(driver)
            _human_read_page(driver, min_time=5, max_time=12)

        # Click on an artist or track (50% chance)
        if random.random() < 0.5:
            try:
                link_selectors = [
                    "a[href*='/artist/']",
                    "a[href*='/album/']",
                    "a.d-track__title",
                    ".playlist__title-link",
                    "a.artist__name",
                    "a[href*='/track/']",
                ]
                links = []
                for sel in link_selectors:
                    found = driver.find_elements(By.CSS_SELECTOR, sel)
                    links.extend([l for l in found if l.is_displayed()])
                    if len(links) >= 6:
                        break

                if links:
                    chosen = random.choice(links[:8])
                    ActionChains(driver).move_to_element(chosen).pause(
                        random.uniform(0.3, 0.6)
                    ).click().perform()
                    _fast_sleep(3, 5)
                    _human_read_page(driver, min_time=5, max_time=15)
                    _safe_back(driver)
                    _fast_sleep(1, 2)
            except:
                pass

        logger.info("🎵 Yandex Music browsed")
        return True

    except Exception as e:
        logger.warning(f"Error browsing Yandex Music: {e}")
        return False


def _yandex_search_click_through(driver, search_queries_pool: list) -> bool:
    """Perform realistic search with click-through on organic results.
    
    This is a KEY behavioral signal: search → click result → read page → back to SERP → 
    maybe click another result. Builds strong organic search behavior profile.
    """
    try:
        query = random.choice(search_queries_pool) if search_queries_pool else random.choice(YANDEX_SEARCH_QUERIES)

        # Open Yandex search directly
        encoded_query = query.replace(' ', '+')
        if not _safe_get(driver, f"https://yandex.ru/search/?text={encoded_query}"):
            return False
        _fast_sleep(3, 5)
        _try_dismiss_cookies(driver)

        # Read SERP first (like a real user scanning results)
        _human_read_page(driver, min_time=3, max_time=7)

        # Collect organic result links
        result_selectors = [
            "a.OrganicTitle-Link",
            "li.serp-item a.link",
            ".organic__url",
            "a[data-cid] h2 a",
            ".Organic a.Path-Item",
            "h2 a[href]:not([href*='yandex']):not([href*='direct'])",
        ]
        results = []
        for sel in result_selectors:
            found = driver.find_elements(By.CSS_SELECTOR, sel)
            results.extend([r for r in found if r.is_displayed() and r.size.get('height', 0) > 5])

        if not results:
            logger.debug("No organic results found for click-through")
            _human_read_page(driver, min_time=3, max_time=6)
            return True

        # Click 1-3 results with natural reading and back behavior
        clicks_to_do = random.randint(1, min(3, len(results)))
        clicked_indices = set()

        for click_num in range(clicks_to_do):
            # Pick a result (prefer top results but not always #1)
            available = [i for i in range(min(8, len(results))) if i not in clicked_indices]
            if not available:
                break
            idx = random.choice(available)
            clicked_indices.add(idx)

            try:
                result = results[idx]
                # Scroll the result into view and move cursor to it
                ActionChains(driver).move_to_element(result).pause(
                    random.uniform(0.4, 0.8)  # Reading the snippet before clicking
                ).click().perform()
                _fast_sleep(2, 5)

                # Read the page we landed on
                read_time = random.uniform(8, 25) * SPEED_FACTOR
                _human_read_page(driver, min_time=int(read_time * 0.6), max_time=int(read_time))

                # Go back to SERP
                _safe_back(driver)
                _fast_sleep(1, 3)

                # Scroll a bit on SERP (looking for next result)
                if click_num < clicks_to_do - 1:
                    _smooth_scroll(driver, "down", random.randint(100, 400))
                    _fast_sleep(1, 3)

            except Exception:
                _safe_back(driver)
                _fast_sleep(1, 2)
                continue

        # Sometimes scroll SERP to page 2 (15% chance)
        if random.random() < 0.15:
            try:
                next_selectors = [
                    "a.Pager-Item_type_next",
                    "a[aria-label='Следующая страница']",
                    ".pager__item_kind_next a",
                    "a.pager__button_kind_next",
                ]
                for sel in next_selectors:
                    nexts = driver.find_elements(By.CSS_SELECTOR, sel)
                    visible = [n for n in nexts if n.is_displayed()]
                    if visible:
                        ActionChains(driver).move_to_element(visible[0]).pause(0.3).click().perform()
                        _fast_sleep(2, 4)
                        _human_read_page(driver, min_time=3, max_time=8)
                        break
            except:
                pass

        logger.info(f"🔎 Search click-through completed: '{query}' ({len(clicked_indices)} clicks)")
        return True

    except Exception as e:
        logger.warning(f"Error in search click-through: {e}")
        return False


def _perform_yandex_search(driver, query: str) -> bool:
    """Perform a search on Yandex and browse results."""
    try:
        if not _safe_get(driver, "https://yandex.ru"): return False
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
            if not _safe_get(driver, f"https://yandex.ru/search/?text={encoded_query}"):
                return False
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
                    _safe_back(driver)
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
        if not _safe_get(driver, "https://www.google.com"):
            logger.warning("Error in Google search: Timeout navigating to https://www.google.com")
            return False
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
        if not _safe_get(driver, "https://yandex.ru/maps"): return False
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
                                _safe_back(driver)
                                _fast_sleep(1, 3)
                                break
                    except:
                        pass

                logger.info(f"🗺️ Yandex Maps search completed: '{query}'")
            else:
                # Fallback: direct URL search
                encoded = query.replace(' ', '+')
                if not _safe_get(driver, f"https://yandex.ru/maps/?text={encoded}"):
                    return False
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


# Hard cap per site visit — prevents any single site from consuming the entire chunk budget
_SITE_VISIT_MAX_SECONDS = 60


def _visit_site_with_actions(driver, url: str, site_index: int, total_sites: int) -> float:
    """Visit a site and perform realistic human actions. Returns time spent.
    
    Hard-capped at _SITE_VISIT_MAX_SECONDS to prevent any single site from
    hanging the entire chunk (which leads to SIGKILL).
    """
    visit_start = time.time()
    visit_deadline = visit_start + _SITE_VISIT_MAX_SECONDS

    def _visit_time_left():
        return visit_deadline - time.time()

    try:
        # Clear any pending navigation left over from navigate_to_url
        try:
            driver.execute_cdp_cmd("Page.stopLoading")
        except Exception:
            pass

        # Try to dismiss cookie banners
        _try_dismiss_cookies(driver)
        _fast_sleep(0.3, 1.0)

        if _visit_time_left() < 5:
            return time.time() - visit_start

        # Decide how long to stay based on site type
        if any(y in url for y in ["yandex", "ya.ru", "dzen.ru", "kinopoisk"]):
            min_time, max_time = 5, 15  # Yandex ecosystem
        elif any(s in url for s in ["vk.com", "ok.ru", "youtube", "pikabu", "habr"]):
            min_time, max_time = 4, 12  # Social/content sites
        elif any(s in url for s in ["ozon", "wildberries", "avito", "market", "dns-shop", "mvideo"]):
            min_time, max_time = 4, 15  # E-commerce
        else:
            min_time, max_time = 3, 10  # General sites

        # Cap read time to remaining visit budget
        max_time = min(max_time, _visit_time_left() - 2)
        if max_time < 2:
            return time.time() - visit_start

        # Read the page (scroll, pause, mouse moves)
        _human_read_page(driver, min_time=min(min_time, max_time), max_time=max_time)

        # Sometimes click on internal links (20% chance)
        if random.random() < 0.2 and _visit_time_left() > 10:
            try:
                links = driver.find_elements(By.CSS_SELECTOR, "a[href]")
                clickable_links = []
                current_domain = url.split("//")[-1].split("/")[0].replace("www.", "")
                for link in links[:15]:
                    if _visit_time_left() < 5:
                        break
                    try:
                        href = link.get_attribute("href") or ""
                        if (current_domain in href and link.is_displayed()
                                and link.size.get('height', 0) > 5
                                and not href.endswith(('.pdf', '.zip', '.exe', '.doc'))):
                            clickable_links.append(link)
                    except StaleElementReferenceException:
                        continue
                    except Exception:
                        break

                if clickable_links:
                    chosen_link = random.choice(clickable_links[:10])
                    ActionChains(driver).move_to_element(chosen_link).pause(
                        random.uniform(0.2, 0.5)
                    ).click().perform()
                    _fast_sleep(1, 3)
                    remaining = _visit_time_left() - 5
                    if remaining > 2:
                        _human_read_page(driver, min_time=2, max_time=min(6, remaining))
                    _safe_back(driver)
                    _fast_sleep(0.5, 1.5)
            except:
                pass

    except Exception as e:
        logger.debug(f"Minor error during site actions on {url}: {e}")

    return time.time() - visit_start

@shared_task(base=BaseTask, bind=True, max_retries=1, default_retry_delay=60, time_limit=300, soft_time_limit=270)
def warmup_profile_task(self, profile_id: int, duration_minutes: int = None, sites_list: List[str] = None):
    """
    Multi-session warmup orchestrator.
    
    Instead of visiting all sites in one long-lived browser session (which
    gets SIGKILL after 75 min), this task plans the session and delegates
    actual browsing to ``warmup_chunk_task`` — each chunk opens a fresh
    browser, visits ≤WARMUP_CHUNK_MAX_SITES sites, closes the browser
    cleanly, and chains to the next chunk.

    time_limit=300s (5 min) — orchestrator itself is lightweight, just planning.
    """
    try:
        # Get profile from database and determine current stage
        with get_db_session() as db:
            profile_obj = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
            if not profile_obj:
                raise ValueError(f"Profile {profile_id} not found")

            current_stage = profile_obj.get_next_warmup_stage()
            is_rewarmup = profile_obj.warmup_completed

            profile_persona_data = profile_obj.persona_data

            profile_obj.status = "warming_up"
            db.commit()

        logger.info(f"🔥 Warmup ORCHESTRATOR profile {profile_id} — STAGE {current_stage} {'(re-warmup)' if is_rewarmup else ''}")

        # Build site list — 50 domains in 1 session
        sites_count = 50
        if not sites_list:
            sites_list = _build_warmup_site_list(profile_id, count=sites_count, stage=current_stage, persona_data=profile_persona_data)

        # === Build pre-browsing actions plan ===
        # No Yandex services (captcha not handled during warmup)
        pre_actions = []

        if random.random() < 0.5:
            pre_actions.append("youtube")
        if random.random() < 0.4:
            pre_actions.append("google_search")

        # === Split pre_actions into chunks (max 2 actions per chunk) ===
        PRE_ACTION_CHUNK_SIZE = 2
        pre_action_chunks = []
        for i in range(0, len(pre_actions), PRE_ACTION_CHUNK_SIZE):
            pre_action_chunks.append(pre_actions[i:i + PRE_ACTION_CHUNK_SIZE])

        # === Split site list into chunks of WARMUP_CHUNK_MAX_SITES ===
        site_chunks = []
        for i in range(0, len(sites_list), WARMUP_CHUNK_MAX_SITES):
            site_chunks.append(sites_list[i:i + WARMUP_CHUNK_MAX_SITES])

        # === Build the chunk plan ===
        # Each chunk = {"pre_actions": [...], "sites": [...]}
        all_chunks = []
        for idx, site_chunk in enumerate(site_chunks):
            chunk_pre = pre_action_chunks[idx] if idx < len(pre_action_chunks) else []
            all_chunks.append({
                "pre_actions": chunk_pre,
                "sites": site_chunk,
            })
        # Extra pre-action chunks without sites
        for idx in range(len(site_chunks), len(pre_action_chunks)):
            all_chunks.append({
                "pre_actions": pre_action_chunks[idx],
                "sites": [],
            })

        total_chunks = len(all_chunks)
        logger.info(
            f"📋 Warmup plan for profile {profile_id}: "
            f"{len(pre_actions)} pre-actions, {len(sites_list)} sites → {total_chunks} chunks "
            f"(max {WARMUP_CHUNK_MAX_SITES} sites/chunk)"
        )

        # Fire the first chunk — it will chain to the next
        warmup_chunk_task.apply_async(
            args=[profile_id, current_stage, is_rewarmup, all_chunks, 0, total_chunks],
            queue='warmup'
        )

        return {
            "status": "orchestrated",
            "profile_id": profile_id,
            "stage": current_stage,
            "total_chunks": total_chunks,
            "total_sites": len(sites_list),
            "total_pre_actions": len(pre_actions),
        }

    except Exception as e:
        logger.error(f"Error in warmup orchestrator for profile {profile_id}: {e}")
        try:
            with get_db_session() as db:
                profile_obj = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
                if profile_obj:
                    profile_obj.status = "created" if not profile_obj.warmup_completed else "warmed"
                    db.commit()
        except:
            pass
        raise


def _execute_pre_action(driver, action, search_queries_pool):
    """Execute a single pre-browsing action. Returns (time_spent, searches, maps)."""
    searches = 0
    maps = 0
    time_spent = 0
    try:
        if action == "youtube":
            if _watch_youtube_video(driver):
                time_spent += 25
            _fast_sleep(1, 3)
        elif action == "google_search":
            query = random.choice(GOOGLE_SEARCH_QUERIES)
            if _perform_google_search_warmup(driver, query):
                searches += 1
                time_spent += 15
            _fast_sleep(2, 5)
    except Exception as act_err:
        logger.warning(f"⚠️ Pre-action '{action}' failed: {act_err}")

    return time_spent, searches, maps


@shared_task(base=BaseTask, bind=True, max_retries=1, default_retry_delay=30,
             time_limit=480, soft_time_limit=420,
             reject_on_worker_lost=False, acks_on_failure_or_timeout=True)
def warmup_chunk_task(self, profile_id: int, current_stage: int, is_rewarmup: bool,
                      all_chunks: list, chunk_index: int, total_chunks: int):
    """
    Execute ONE warmup chunk: open browser, do pre-actions + visit sites, close browser.
    Then chain to the next chunk. The last chunk finalises the warmup session in DB.
    
    Wall-clock budget (CHUNK_MAX_DURATION=300s) stops visiting new sites before
    Celery's hard time_limit forces SIGKILL. reject_on_worker_lost=False prevents
    infinite redelivery loops when a worker does get killed.
    """
    browser_manager = None
    browser_id = None
    _profile_dir_for_cleanup = None
    chunk_failed = False

    chunk_data = all_chunks[chunk_index]
    pre_actions = chunk_data.get("pre_actions", [])
    sites_to_visit = chunk_data.get("sites", [])
    is_last_chunk = (chunk_index == total_chunks - 1)

    logger.info(
        f"🔥 Chunk {chunk_index + 1}/{total_chunks} for profile {profile_id} stage {current_stage}: "
        f"{len(pre_actions)} pre-actions, {len(sites_to_visit)} sites"
    )

    successful_visits = 0
    sites_visited = 0
    searches_done = 0
    maps_browsed = 0
    total_time_spent = 0
    start_time = time.time()

    # Hard wall-clock alarm: guarantees the chunk exits before Celery's
    # time_limit sends SIGKILL. This breaks through ANY blocking Playwright
    # call (bounding_box, mouse.move, evaluate, etc.) when Chrome hangs.
    CHUNK_MAX_DURATION = 300  # seconds — stop early, leave 180s buffer for cleanup
    _old_alarm_handler = signal.getsignal(signal.SIGALRM)
    def _chunk_alarm(signum, frame):
        raise TimeoutError(f"Chunk alarm: wall-clock budget ({CHUNK_MAX_DURATION}s) exceeded")
    signal.signal(signal.SIGALRM, _chunk_alarm)
    signal.alarm(CHUNK_MAX_DURATION)

    try:
        # Load profile data
        with get_db_session() as db:
            profile_obj = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
            if not profile_obj:
                raise ValueError(f"Profile {profile_id} not found")
            if not profile_obj.is_active:
                raise ValueError(f"Profile {profile_id} is not active")

            profile_name = profile_obj.name
            # Keep updated_at fresh so auto_fix_stuck doesn't reset us
            profile_obj.updated_at = datetime.utcnow()
            db.commit()

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
            profile_platform = profile_obj.platform
            profile_is_mobile = profile_obj.is_mobile or False
            profile_canvas_fp = profile_obj.canvas_fingerprint
            profile_webgl_fp = profile_obj.webgl_fingerprint
            profile_audio_fp = profile_obj.audio_fingerprint
            profile_screen_fp = profile_obj.screen_fingerprint

        # Setup proxy
        proxy_manager = ProxyManager()
        proxy_manager.load_proxies_from_db()
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

        if not proxy_data:
            raise RuntimeError(f"No proxy available for profile {profile_id}")

        # Build profile data for browser
        profile_generator = ProfileGenerator()
        profile_data = profile_generator.generate_profile(profile_name, is_mobile=profile_is_mobile)
        profile_data.update({
            'user_agent': profile_user_agent or profile_data['user_agent'],
            'viewport': {'width': profile_viewport_width, 'height': profile_viewport_height},
            'timezone': profile_timezone,
            'language': profile_language,
            'platform': profile_platform or profile_data.get('platform', 'Win32'),
            'images_enabled': True,
        })
        # Apply stored fingerprints
        if profile_webgl_fp:
            import json as _json
            try:
                webgl_dict = _json.loads(profile_webgl_fp) if isinstance(profile_webgl_fp, str) else profile_webgl_fp
                if webgl_dict and isinstance(webgl_dict, dict) and 'unmaskedVendor' in webgl_dict:
                    profile_data['webgl_fingerprint'] = webgl_dict
            except (ValueError, TypeError):
                pass
        if profile_canvas_fp:
            profile_data['canvas_fingerprint'] = profile_canvas_fp
        if profile_audio_fp:
            profile_data['audio_fingerprint'] = profile_audio_fp
        if profile_screen_fp and isinstance(profile_screen_fp, dict):
            if 'css_media' in profile_screen_fp:
                profile_data['css_media'] = profile_screen_fp['css_media']
            if 'feature_flags' in profile_screen_fp:
                profile_data['feature_flags'] = profile_screen_fp['feature_flags']
            if 'audio_properties' in profile_screen_fp:
                profile_data['audio_properties'] = profile_screen_fp['audio_properties']
            if 'speech_voices' in profile_screen_fp:
                profile_data['speech_voices'] = profile_screen_fp['speech_voices']
            if 'sensor' in profile_screen_fp:
                profile_data['sensor'] = profile_screen_fp['sensor']
            for key in ('connection_info', 'storage_quota', 'heap_size', 'system_colors',
                        'system_fonts', 'codecs', 'keyboard_layout', 'fonts'):
                if key in profile_screen_fp:
                    profile_data[key] = profile_screen_fp[key]
            for _hw_key in ('hardware_concurrency', 'device_memory', 'max_touch_points', 'do_not_track'):
                if _hw_key in profile_screen_fp:
                    profile_data[_hw_key] = profile_screen_fp[_hw_key]

        from app.config import settings as _settings
        _profile_dir_for_cleanup = os.path.join(_settings.browser_user_data_dir, profile_data['name'])

        # Create browser session
        browser_manager = BrowserManager()
        browser_id = browser_manager.create_browser_session(profile_data, proxy_data)
        driver = browser_manager.active_browsers.get(browser_id)
        if not driver:
            raise RuntimeError(f"Failed to get driver for session {browser_id}")

        logger.info(f"🌐 Browser session {browser_id} created for chunk {chunk_index + 1}")

        # Wall-clock budget: stop visiting new sites before hard time_limit kills us.
        # time_limit=480, soft_time_limit=420 — but Playwright blocking calls can't be
        # interrupted by SoftTimeLimitExceeded signal, so we must check manually.
        def _chunk_time_remaining():
            return CHUNK_MAX_DURATION - (time.time() - start_time)

        # === Execute pre-actions ===
        for action in pre_actions:
            if _chunk_time_remaining() < 60:
                logger.warning(f"⏰ Budget exhausted before pre-action '{action}', skipping")
                break
            t, s, m = _execute_pre_action(driver, action, [])
            total_time_spent += t
            searches_done += s
            maps_browsed += m

        # === Visit sites ===
        consecutive_failures = 0
        for i, site_url in enumerate(sites_to_visit):
            remaining = _chunk_time_remaining()
            if remaining < 30:
                logger.warning(
                    f"⏰ Wall-clock budget exhausted ({CHUNK_MAX_DURATION}s) after {i}/{len(sites_to_visit)} sites "
                    f"— stopping chunk early to avoid SIGKILL"
                )
                break
            try:
                if browser_manager.navigate_to_url(browser_id, site_url, timeout=min(20, remaining - 10)):
                    sites_visited += 1
                    consecutive_failures = 0
                    visit_time = _visit_site_with_actions(driver, site_url, i, len(sites_to_visit))
                    total_time_spent += visit_time
                    successful_visits += 1
                    logger.info(f"✅ [{successful_visits}/{len(sites_to_visit)}] {site_url} — {visit_time:.1f}s")
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
                        logger.warning(f"🛑 {consecutive_failures} consecutive failures — stopping chunk early")
                        break
            except Exception as site_error:
                logger.error(f"Error visiting {site_url}: {site_error}")
                consecutive_failures += 1
                time.sleep(1)
                if consecutive_failures >= 3:
                    break
                continue

        actual_duration = time.time() - start_time
        logger.info(
            f"✅ Chunk {chunk_index + 1}/{total_chunks} done for profile {profile_id} in {actual_duration:.0f}s: "
            f"{successful_visits}/{sites_visited} sites, {searches_done} searches, {maps_browsed} maps"
        )

        # Track total successful visits across all chunks in Redis
        try:
            r = _get_warmup_redis()
            r.incrby(f"warmup:visits:{profile_id}:{current_stage}", successful_visits)
            r.expire(f"warmup:visits:{profile_id}:{current_stage}", 7200)  # 2h TTL
        except Exception:
            pass

    except SoftTimeLimitExceeded:
        logger.error(f"⏰ Chunk {chunk_index + 1} soft time limit for profile {profile_id}")
        chunk_failed = True

    except TimeoutError as te:
        logger.error(f"⏰ Chunk {chunk_index + 1} ALARM for profile {profile_id}: {te}")
        chunk_failed = True

    except Exception as e:
        logger.error(f"Error in chunk {chunk_index + 1} for profile {profile_id}: {e}")
        chunk_failed = True

    finally:
        # Keep alarm ACTIVE during cleanup — set a shorter cleanup alarm (120s).
        # Previous bug: signal.alarm(0) cancelled the alarm BEFORE close_browser_session(),
        # so if Playwright API hung during cleanup, there was no safety net → SIGKILL at 900s.
        CLEANUP_TIMEOUT = 120
        signal.alarm(CLEANUP_TIMEOUT)

        # ALWAYS close browser — this is the key fix
        if browser_manager and browser_id:
            try:
                browser_manager.close_browser_session(browser_id)
                logger.info(f"✅ Browser session {browser_id} closed cleanly")
            except TimeoutError:
                logger.error(f"⏰ Browser cleanup alarm for {browser_id} — force killing")
                # Alarm fired during cleanup — force-kill by profile dir
                if _profile_dir_for_cleanup:
                    try:
                        import subprocess as _sp
                        _sp.run(['pkill', '-9', '-f', _profile_dir_for_cleanup], capture_output=True, timeout=5)
                    except Exception:
                        pass
            except Exception as e:
                logger.error(f"Error closing browser session: {e}")
        elif _profile_dir_for_cleanup:
            try:
                if browser_manager and hasattr(browser_manager, '_kill_chrome_by_profile_dir'):
                    browser_manager._kill_chrome_by_profile_dir(_profile_dir_for_cleanup)
                else:
                    import subprocess as _sp
                    _sp.run(['pkill', '-9', '-f', _profile_dir_for_cleanup], capture_output=True, timeout=5)
            except Exception as cleanup_err:
                logger.warning(f"Cleanup by profile dir failed: {cleanup_err}")

        # NOW cancel alarm and restore handler — AFTER cleanup is done
        signal.alarm(0)
        signal.signal(signal.SIGALRM, _old_alarm_handler)

    # === Chain to next chunk or finalise ===
    if is_last_chunk:
        # Get total successful visits across all chunks from Redis
        total_successful = 0
        try:
            r = _get_warmup_redis()
            total_successful = int(r.get(f"warmup:visits:{profile_id}:{current_stage}") or 0)
            r.delete(f"warmup:visits:{profile_id}:{current_stage}")
        except Exception:
            pass
        _finalise_warmup_session(profile_id, current_stage, is_rewarmup, total_successful)
    else:
        # Chain to next chunk with a small delay (let Chrome processes clean up)
        # Use priority=3 so chain chunks go before new warmup tasks (default priority=0)
        delay_seconds = random.randint(3, 8)
        warmup_chunk_task.apply_async(
            args=[profile_id, current_stage, is_rewarmup, all_chunks, chunk_index + 1, total_chunks],
            countdown=delay_seconds,
            queue='warmup',
        )

    # Signal progress to host watchdog after every chunk (not just session end)
    try:
        _get_warmup_redis().incr("warmup:completions")
    except Exception:
        pass

    return {
        "status": "chunk_done",
        "profile_id": profile_id,
        "chunk": chunk_index + 1,
        "total_chunks": total_chunks,
        "is_last": is_last_chunk,
        "successful_visits": successful_visits,
        "searches": searches_done,
    }


def _finalise_warmup_session(profile_id: int, current_stage: int, is_rewarmup: bool, total_successful_visits: int = 0):
    """Update profile DB record after all chunks of a warmup session are done."""
    try:
        with get_db_session() as db:
            profile_obj = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
            if not profile_obj:
                logger.warning(f"Profile {profile_id} not found during finalise")
                return

            # Quality check: if too few sites visited, don't count this session
            if total_successful_visits < MIN_WARMUP_VISITS_PER_SESSION:
                logger.warning(
                    f"⚠️ Profile {profile_id} stage {current_stage}: only {total_successful_visits} "
                    f"successful visits (need {MIN_WARMUP_VISITS_PER_SESSION}). Session NOT counted, will retry."
                )
                profile_obj.status = "created" if not profile_obj.warmup_completed else "warmed"
                profile_obj.last_used_at = datetime.utcnow()
                db.commit()
                return

            profile_obj.warmup_sessions_count = (profile_obj.warmup_sessions_count or 0) + 1
            profile_obj.last_used_at = datetime.utcnow()

            if not is_rewarmup:
                profile_obj.warmup_stage = current_stage

                if not profile_obj.first_warmup_at:
                    profile_obj.first_warmup_at = datetime.utcnow()

                if current_stage >= MIN_WARMUP_SESSIONS:
                    hours_since_first = 0
                    if profile_obj.first_warmup_at:
                        hours_since_first = (datetime.utcnow() - profile_obj.first_warmup_at).total_seconds() / 3600

                    if hours_since_first >= MIN_WARMUP_HOURS_SPREAD:
                        profile_obj.warmup_completed = True
                        profile_obj.status = "warmed"
                        logger.info(
                            f"✅ Profile {profile_id} FULLY WARMED after {current_stage} sessions "
                            f"over {hours_since_first:.1f} hours"
                        )
                    else:
                        profile_obj.status = "created"
                        remaining_hours = MIN_WARMUP_HOURS_SPREAD - hours_since_first
                        retry_minutes = max(30, int(remaining_hours * 60))
                        logger.info(
                            f"⏳ Profile {profile_id} completed stage {current_stage} but only "
                            f"{hours_since_first:.1f}h since first warmup (need {MIN_WARMUP_HOURS_SPREAD}h). "
                            f"Will retry in {retry_minutes} min."
                        )
                        warmup_profile_task.apply_async(
                            args=[profile_id],
                            eta=datetime.utcnow() + timedelta(minutes=retry_minutes),
                            queue='warmup'
                        )
                else:
                    profile_obj.status = "created"
                    next_delay_min = max(5, WARMUP_SESSION_INTERVAL_HOURS * 60)
                    logger.info(
                        f"📋 Profile {profile_id} completed stage {current_stage}/{MIN_WARMUP_SESSIONS}. "
                        f"Next session scheduled in {next_delay_min} min."
                    )
                    warmup_profile_task.apply_async(
                        args=[profile_id],
                        eta=datetime.utcnow() + timedelta(minutes=next_delay_min),
                        queue='warmup'
                    )
            else:
                if profile_obj.warmup_stage < current_stage:
                    profile_obj.warmup_stage = current_stage
                    logger.info(f"📈 Profile {profile_id} re-warmup advanced to stage {current_stage}")
                profile_obj.status = "warmed"

            db.commit()
            logger.info(f"✅ Warmup session finalised for profile {profile_id} stage {current_stage}")

            # Signal to host watchdog that warmup is making progress
            try:
                _get_warmup_redis().incr("warmup:completions")
            except Exception:
                pass

    except Exception as e:
        logger.error(f"Error finalising warmup for profile {profile_id}: {e}")
        try:
            with get_db_session() as db:
                p = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
                if p:
                    p.status = "created" if not p.warmup_completed else "warmed"
                    db.commit()
        except:
            pass



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


@shared_task(base=BaseTask, bind=True, time_limit=4500, soft_time_limit=4440)
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
            ).order_by(BrowserProfile.warmup_stage.desc(), BrowserProfile.last_used_at.asc().nullsfirst()).limit(20).all()

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

        # Mark profiles as warming_up BEFORE scheduling to prevent duplicate picks
        all_ids_to_schedule = profile_ids_next + profile_ids_rewarm
        if all_ids_to_schedule:
            with get_db_session() as db:
                db.query(BrowserProfile).filter(
                    BrowserProfile.id.in_(all_ids_to_schedule)
                ).update({BrowserProfile.status: "warming_up"}, synchronize_session=False)
                db.commit()

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
            # First, reset profiles stuck in 'warming_up' for >45 min
            # Stage 3 profiles can take 20+ min (4 chunks × 5 min each)
            stuck_threshold = now - timedelta(minutes=45)
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

            # Allow up to 20 concurrent warmup tasks to keep queue manageable
            MAX_WARMUP_CONCURRENT = 20
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
                BrowserProfile.warmup_stage.desc(),  # prioritize closest to completion
                BrowserProfile.id.asc()  # oldest first
            ).limit(slots).all()

            profile_ids = [p.id for p in new_profiles]

        if not profile_ids:
            logger.info(f"📋 No new profiles need initial warmup (reset={reset_count})")
            return {"scheduled": 0, "reset": reset_count}

        # Mark profiles as warming_up BEFORE scheduling to prevent duplicate picks
        with get_db_session() as db:
            db.query(BrowserProfile).filter(
                BrowserProfile.id.in_(profile_ids)
            ).update({BrowserProfile.status: "warming_up"}, synchronize_session=False)
            db.commit()

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
        # Stage 3 warmup can take 20+ min (multiple chunks)
        stuck_threshold = timedelta(minutes=45)

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


@shared_task(base=BaseTask)
def auto_maintain_profile_pool():
    """
    Auto-create profiles to keep total warmed/active pool at ~500.
    Runs every 10 minutes via Celery Beat.
    Creates profiles in batches when warmed count drops below threshold.
    Uses ProfileGenerator for full fingerprint coverage (no Firefox UAs).
    """
    TARGET_POOL_SIZE = 2000
    BATCH_SIZE = 400
    # Don't create more if too many profiles haven't even started warming yet
    MAX_STAGE0_BACKLOG = 200

    try:
        with get_db_session() as db:
            warmed_count = db.query(BrowserProfile).filter(
                BrowserProfile.is_active == True,
                BrowserProfile.warmup_completed == True,
            ).count()

            warming_count = db.query(BrowserProfile).filter(
                BrowserProfile.is_active == True,
                BrowserProfile.warmup_completed == False,
                BrowserProfile.status.in_(['created', 'warming_up']),
            ).count()

            # Check how many stage 0 profiles are waiting (never started)
            stage0_count = db.query(BrowserProfile).filter(
                BrowserProfile.is_active == True,
                BrowserProfile.warmup_completed == False,
                BrowserProfile.warmup_stage == 0,
            ).count()

            total_pipeline = warmed_count + warming_count

            if total_pipeline >= TARGET_POOL_SIZE:
                logger.info(
                    f"📋 Profile pool OK: {warmed_count} warmed + {warming_count} warming = {total_pipeline} (target={TARGET_POOL_SIZE})"
                )
                return {"status": "ok", "warmed": warmed_count, "warming": warming_count, "created": 0}

            if stage0_count >= MAX_STAGE0_BACKLOG:
                logger.info(
                    f"⏭️ Too many stage-0 profiles waiting ({stage0_count}), skipping creation until they warm up"
                )
                return {"status": "backlog", "warmed": warmed_count, "warming": warming_count, "stage0_backlog": stage0_count, "created": 0}

            need = min(TARGET_POOL_SIZE - total_pipeline, BATCH_SIZE)
            logger.info(
                f"📦 Profile pool low: {warmed_count} warmed + {warming_count} warming = {total_pipeline}. Creating {need} new profiles..."
            )

            max_id = db.query(func.max(BrowserProfile.id)).scalar() or 0

            from core.profile_generator import ProfileGenerator
            import json as _json
            pg = ProfileGenerator()

            rows = []
            for i in range(need):
                profile_name = f"Profile-{max_id + i + 1}"
                # ~20% mobile
                is_mobile = random.random() < 0.2
                p = pg.generate_profile(profile_name, is_mobile=is_mobile)

                viewport = p.get("viewport", {})
                screen = p.get("screen", {})

                screen_fp = {
                    "screen": screen,
                    "css_media": p.get("css_media", {}),
                    "feature_flags": p.get("feature_flags", {}),
                    "audio_properties": p.get("audio_properties", {}),
                    "speech_voices": p.get("speech_voices", []),
                    "connection_info": p.get("connection_info", {}),
                    "storage_quota": p.get("storage_quota", 599720927232),
                    "heap_size": p.get("heap_size", 4294705152),
                    "system_colors": p.get("system_colors", {}),
                    "system_fonts": p.get("system_fonts", []),
                    "codecs": p.get("codecs", []),
                    "keyboard_layout": p.get("keyboard_layout", []),
                    "fonts": p.get("fonts", []),
                }
                if p.get("sensor"):
                    screen_fp["sensor"] = p["sensor"]

                rows.append({
                    "name": profile_name,
                    "user_agent": p["user_agent"],
                    "viewport_width": viewport.get("width", 1366),
                    "viewport_height": viewport.get("height", 768),
                    "timezone": p.get("timezone", "Europe/Moscow"),
                    "language": p.get("language", "ru-RU"),
                    "platform": p.get("platform", "Win32"),
                    "is_mobile": is_mobile,
                    "canvas_fingerprint": p.get("canvas_fingerprint", ""),
                    "webgl_fingerprint": _json.dumps(p.get("webgl_fingerprint", {})),
                    "audio_fingerprint": p.get("audio_fingerprint", ""),
                    "screen_fingerprint": screen_fp,
                    "status": "created",
                    "is_active": True,
                    "warmup_completed": False,
                    "warmup_sessions_count": 0,
                    "warmup_time_spent": 0,
                    "total_sessions": 0,
                    "successful_sessions": 0,
                    "failed_sessions": 0,
                    "webrtc_leak_protect": True,
                    "geolocation_enabled": False,
                    "notifications_enabled": False,
                })

            db.bulk_insert_mappings(BrowserProfile, rows)
            db.commit()

            # Trigger warmup for new profiles
            try:
                last_id = db.query(func.max(BrowserProfile.id)).scalar()
                first_id = last_id - need + 1
                new_ids = list(range(first_id, last_id + 1))
                generate_warmup_sites_task.delay(new_ids)
            except Exception as ws_err:
                logger.warning(f"Warmup sites generation trigger failed: {ws_err}")

            logger.info(f"✅ Auto-created {need} new profiles with full fingerprints (pool: {total_pipeline} → {total_pipeline + need})")
            return {"status": "created", "warmed": warmed_count, "warming": warming_count, "created": need}

    except Exception as e:
        logger.error(f"Error in auto_maintain_profile_pool: {e}")
        return {"error": str(e)}


@shared_task(base=BaseTask)
def cleanup_orphaned_chrome_processes():
    """Kill orphaned Chrome processes with no active browser session.
    
    Runs every 10 minutes via Celery Beat.
    """
    try:
        from core.browser_manager import cleanup_orphaned_chrome
        killed = cleanup_orphaned_chrome()
        return {"killed": killed}
    except Exception as e:
        logger.error(f"Error in cleanup_orphaned_chrome_processes: {e}")


# ---------------------------------------------------------------------------
# Warmup watchdog — self-healing + Telegram alerts
# ---------------------------------------------------------------------------
_WATCHDOG_CHECK_WINDOW = 180  # 3 min — same as beat interval
_WATCHDOG_MAX_ZERO_RUNS = 3   # alert after 3 consecutive zero-completion checks (~9 min)

def _send_telegram_alert(message: str):
    """Send alert via Telegram if configured.
    
    Env vars: TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
    Rate-limited: max 1 message per 5 min per unique prefix.
    """
    import os, hashlib, requests as _req
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    # Rate-limit via Redis
    prefix = hashlib.md5(message[:40].encode()).hexdigest()[:8]
    try:
        r = _get_warmup_redis()
        key = f"tg_alert:{prefix}"
        if r.get(key):
            return  # already sent recently
        r.setex(key, 300, "1")  # 5 min cooldown
    except Exception:
        pass
    try:
        _req.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"Telegram send failed: {e}")


@shared_task(base=BaseTask)
def warmup_watchdog():
    """Self-healing warmup watchdog. Runs every 3 min via Celery Beat.
    
    Tracks chunk completions via Redis counter ``warmup:completions``.
    If no chunks complete for several consecutive checks AND there are profiles
    that still need warming, the watchdog:
      1. Purges the warmup queue (break redelivery loops).
      2. Resets stuck warming_up profiles back to created.
      3. Sends a Telegram alert (if configured).
    
    Redis keys used:
      warmup:completions        — counter incremented by warmup_chunk_task
      warmup:wd:prev_completions — snapshot from previous watchdog run
      warmup:wd:zero_runs       — consecutive runs with 0 new completions
    """
    try:
        r = _get_warmup_redis()
        now_completions = int(r.get("warmup:completions") or 0)
        prev_completions = int(r.get("warmup:wd:prev_completions") or 0)
        new_completions = now_completions - prev_completions

        # Save for next run
        r.set("warmup:wd:prev_completions", now_completions)

        if new_completions > 0:
            # Healthy — reset zero counter
            r.set("warmup:wd:zero_runs", 0)
            logger.info(f"✅ Watchdog: {new_completions} chunk completions in last window (total={now_completions})")
            return {"status": "healthy", "new_completions": new_completions}

        # Zero completions — check if there's actually work to do
        with get_db_session() as db:
            need_warmup = db.query(func.count(BrowserProfile.id)).filter(
                BrowserProfile.warmup_completed == False,
                BrowserProfile.is_active == True,
            ).scalar() or 0
            warming_now = db.query(func.count(BrowserProfile.id)).filter(
                BrowserProfile.status == "warming_up",
            ).scalar() or 0

        if need_warmup == 0:
            r.set("warmup:wd:zero_runs", 0)
            logger.info("✅ Watchdog: no profiles need warmup — all done")
            return {"status": "idle", "need_warmup": 0}

        # Increment zero-completion counter
        zero_runs = int(r.get("warmup:wd:zero_runs") or 0) + 1
        r.set("warmup:wd:zero_runs", zero_runs)

        logger.warning(
            f"⚠️ Watchdog: 0 completions for {zero_runs} consecutive checks "
            f"(need_warmup={need_warmup}, warming_now={warming_now})"
        )

        if zero_runs < _WATCHDOG_MAX_ZERO_RUNS:
            return {"status": "warning", "zero_runs": zero_runs, "need_warmup": need_warmup}

        # === SELF-HEAL ===
        logger.error(f"🚨 Watchdog: warmup stuck for {zero_runs} checks (~{zero_runs * 3} min). Auto-healing...")

        healed = {"purged": 0, "reset": 0}

        # 1) Purge warmup queue to break redelivery loops
        try:
            from .celery_app import celery_app
            purged = celery_app.control.purge()  # purge default queue
            # Also purge warmup queue specifically
            with celery_app.connection_or_acquire() as conn:
                q = conn.default_channel.queue_declare('warmup', passive=True)
                if hasattr(q, 'message_count'):
                    healed["purged"] = q.message_count
            celery_app.control.discard_all()
            try:
                # Direct Redis LTRIM to clear warmup queue
                r.delete("warmup")
            except Exception:
                pass
            logger.info(f"🧹 Watchdog: purged warmup queue")
        except Exception as e:
            logger.error(f"Watchdog: purge error: {e}")

        # 2) Reset stuck warming_up profiles
        try:
            with get_db_session() as db:
                stuck = db.query(BrowserProfile).filter(
                    BrowserProfile.status == "warming_up"
                ).all()
                for p in stuck:
                    p.status = "created" if not p.warmup_completed else "warmed"
                    p.updated_at = datetime.utcnow()
                    healed["reset"] += 1
                if stuck:
                    db.commit()
                    logger.info(f"🔧 Watchdog: reset {healed['reset']} stuck profiles")
        except Exception as e:
            logger.error(f"Watchdog: reset error: {e}")

        # 3) Reset zero counter so watchdog doesn't repeat heal every 3 min
        r.set("warmup:wd:zero_runs", 0)

        # 4) Telegram alert
        msg = (
            f"🚨 <b>Warmup авто-восстановление</b>\n"
            f"Прогрев завис ({zero_runs * 3}+ мин без завершённых чанков).\n"
            f"Очередь очищена, {healed['reset']} профилей сброшено.\n"
            f"Ожидают прогрева: {need_warmup}"
        )
        _send_telegram_alert(msg)

        return {"status": "healed", **healed, "need_warmup": need_warmup}

    except Exception as e:
        logger.error(f"Watchdog error: {e}")
        return {"error": str(e)}
        return {"error": str(e)}