"""
Drop Domains checker — checks domains from backorder.ru against Yandex Maps.
FAST mode: 1 browser per 5 domains, reduced timeouts, 8 parallel workers.
"""
import os
import re
import json
import time
import random
import logging
import urllib.parse
from datetime import datetime

from celery import shared_task
from core.playwright_driver import (
    By, Keys,
    PlaywrightActionChains as ActionChains,
)

from app.database import get_db_session
from app.models.drop_domain import DropDomain
from app.models import BrowserProfile
from core.browser_manager import BrowserManager
from core.proxy_manager import ProxyManager
from core.profile_generator import ProfileGenerator

logger = logging.getLogger(__name__)

DOMAINS_PER_BROWSER = 5  # domains checked per single browser session
WAVE_SIZE = 40  # batches per dispatch wave (40 * 5 = 200 domains)
WAVE_INTERVAL = 60  # seconds between dispatch waves
REDIS_RUNNING_KEY = "drop_domains:running"


def _get_redis():
    """Get Redis connection."""
    import redis as _redis
    from app.config import settings
    return _redis.Redis(
        host=settings.redis_host, port=settings.redis_port, db=0, decode_responses=True
    )


def is_checking_running() -> bool:
    """Check if domain checking process is currently active."""
    try:
        r = _get_redis()
        return r.get(REDIS_RUNNING_KEY) == "1"
    except Exception:
        return False


def stop_checking():
    """Signal the checking process to stop."""
    try:
        r = _get_redis()
        r.delete(REDIS_RUNNING_KEY)
    except Exception:
        pass


def _parse_maps_page(driver, domain_name):
    """Parse Yandex Maps page for organization info. Returns (has_results, parsed) tuple."""
    page_title = driver.title or ''
    current_url = driver.current_url or ''

    # Chrome error — page didn't load
    if 'chrome-error' in current_url:
        return None, None, page_title, current_url  # signal to skip

    parsed = {
        'company_name': None, 'category': None, 'rating': None,
        'reviews': None, 'address': None, 'phone': None,
        'city': None, 'region': None,
    }

    # All parsing in a single JS call — much faster than multiple find_elements
    try:
        data = driver.execute_script("""
            var r = {};
            // Company name
            var el = document.querySelector('.card-title-view__title, .business-card-view__title');
            r.company_name = el ? el.textContent.trim() : null;
            // Category
            el = document.querySelector('.business-card-title-view__category a, .card-title-view__subtitle-link');
            if (el) { r.category = el.textContent.trim(); }
            else {
                el = document.querySelector('.business-card-title-view__category, .card-title-view__subtitle');
                if (el) {
                    var t = '';
                    for (var c of el.childNodes) {
                        if (c.nodeType === 3) t += c.textContent;
                        else if (c.tagName === 'A') t += c.textContent;
                        else break;
                    }
                    r.category = t.trim() || null;
                }
            }
            // Rating
            el = document.querySelector('.business-rating-badge-view__rating, .card-rating-view__value');
            r.rating = el ? el.textContent.trim() : null;
            // Reviews count
            el = document.querySelector('.business-rating-badge-view__rating-count, .card-rating-view__count');
            r.reviews = el ? el.textContent.trim() : null;
            // Address
            el = document.querySelector('.business-contacts-view__address-link, .card-address-view__value, .toponym-card-title-view__description');
            r.address = el ? el.textContent.trim() : null;
            // Phone
            el = document.querySelector('.card-phones-view__phone-number, .business-contacts-view__phone-number');
            r.phone = el ? el.textContent.trim().split('\\n')[0] : null;
            return r;
        """)
    except Exception:
        data = {}

    if not data or not data.get('company_name'):
        return False, parsed, page_title, current_url

    parsed['company_name'] = data['company_name']
    logger.info(f"✅ {domain_name}: {parsed['company_name']}")

    # Category — from JS or fallback from title
    parsed['category'] = data.get('category')
    if not parsed['category'] and page_title and '—' in page_title:
        parts = page_title.split('—')[0].strip().split(',')
        if len(parts) >= 2:
            parsed['category'] = parts[1].strip()

    # Rating
    rt = data.get('rating')
    if rt:
        nums = re.findall(r'[\d]+[,.]?[\d]*', rt)
        if nums:
            parsed['rating'] = float(nums[0].replace(',', '.'))

    # Reviews
    rv = data.get('reviews')
    if rv:
        nums = re.findall(r'\d+', rv)
        if nums:
            parsed['reviews'] = int(nums[0])

    # Address
    parsed['address'] = data.get('address')

    # Phone — strip "Показать телефон"
    ph = data.get('phone')
    if ph:
        parsed['phone'] = re.sub(r'Показать.*$', '', ph).strip() or None

    # City — from URL or title
    url_match = re.search(r'/maps/\d+/([^/]+)/org/', current_url)
    if url_match:
        parsed['city'] = url_match.group(1).replace('_', ' ').replace('-', ' ').title()
    elif page_title and '—' in page_title:
        before_dash = page_title.split('—')[0].strip()
        if ':' in before_dash.split(',')[0]:
            before_dash = re.sub(r'^[^:]+:\s*', '', before_dash)
        parts = [p.strip() for p in before_dash.split(',')]
        if len(parts) >= 4:
            candidate = parts[2]
            if not re.search(r'(ул\.|пр\.|просп\.|пер\.|ш\.|наб\.|бул\.|пл\.)', candidate, re.I):
                parsed['city'] = candidate
    elif parsed['address']:
        parts = [p.strip() for p in parsed['address'].split(',')]
        if len(parts) >= 3:
            parsed['city'] = parts[-1].strip()

    return True, parsed, page_title, current_url


def _save_domain_result(domain_id, has_results, parsed):
    """Save check result to DB."""
    with get_db_session() as db:
        d = db.query(DropDomain).filter(DropDomain.id == domain_id).first()
        if not d:
            return
        d.maps_checked = True
        d.maps_found = has_results
        d.maps_checked_at = datetime.utcnow()
        if has_results and parsed:
            d.maps_company_name = parsed.get('company_name')
            d.maps_category = parsed.get('category')
            d.maps_rating = parsed.get('rating')
            d.maps_reviews = parsed.get('reviews')
            d.maps_address = parsed.get('address')
            d.maps_phone = parsed.get('phone')
            d.maps_city = parsed.get('city')
            d.maps_region = parsed.get('region')
        db.commit()


def _create_browser_from_info(profile_info, proxy_data):
    """Create browser session from profile info dict + proxy. Returns (browser_manager, browser_id, driver, profile_dir)."""
    pg = ProfileGenerator()
    is_mobile = profile_info.get('is_mobile', False)
    profile_data = pg.generate_profile(profile_info['name'], is_mobile=is_mobile)
    profile_data.update({
        'user_agent': profile_info['user_agent'],
        'viewport': {'width': profile_info['viewport_width'], 'height': profile_info['viewport_height']},
        'timezone': profile_info['timezone'],
        'language': 'ru-RU',
        'platform': profile_info.get('platform') or profile_data.get('platform', 'Win32'),
        'images_enabled': True,
    })

    _db_webgl = profile_info.get('webgl_fingerprint')
    if _db_webgl:
        try:
            wd = json.loads(_db_webgl) if isinstance(_db_webgl, str) else _db_webgl
            if wd and isinstance(wd, dict) and 'unmaskedVendor' in wd:
                profile_data['webgl_fingerprint'] = wd
        except (ValueError, TypeError):
            pass
    if profile_info.get('canvas_fingerprint'):
        profile_data['canvas_fingerprint'] = profile_info['canvas_fingerprint']
    if profile_info.get('audio_fingerprint'):
        profile_data['audio_fingerprint'] = profile_info['audio_fingerprint']

    from app.config import settings as _s
    profile_dir = os.path.join(_s.browser_user_data_dir, profile_data['name'])

    bm = BrowserManager()
    bid = bm.create_browser_session(profile_data, proxy_data)
    driver = bm.active_browsers.get(bid)
    if not driver:
        raise RuntimeError(f"Failed to get driver for {bid}")
    return bm, bid, driver, profile_dir


def check_batch(domain_ids: list):
    """Check a batch of domains (up to DOMAINS_PER_BROWSER) using a single browser session.
    
    Opens browser once, checks each domain by navigating to maps URL, parses, saves.
    """
    browser_manager = None
    browser_id = None
    _profile_dir = None
    results = []

    try:
        # Load domain names
        domains = {}
        with get_db_session() as db:
            objs = db.query(DropDomain).filter(DropDomain.id.in_(domain_ids)).all()
            for o in objs:
                domains[o.id] = o.domain
        if not domains:
            return results

        # Pick random profile
        with get_db_session() as db:
            profile_obj = db.query(BrowserProfile).filter(
                BrowserProfile.warmup_completed == True,
                BrowserProfile.is_active == True,
            ).order_by(BrowserProfile.last_used_at.asc().nullsfirst()).first()
            if not profile_obj:
                logger.error("No profile available")
                for did in domain_ids:
                    _save_domain_result(did, False, None)
                return results
            profile_obj.last_used_at = datetime.utcnow()
            db.commit()

            # Extract all attributes while in session
            profile_info = {
                'name': profile_obj.name,
                'user_agent': profile_obj.user_agent,
                'viewport_width': profile_obj.viewport_width,
                'viewport_height': profile_obj.viewport_height,
                'timezone': profile_obj.timezone,
                'language': profile_obj.language,
                'platform': profile_obj.platform,
                'is_mobile': profile_obj.is_mobile or False,
                'canvas_fingerprint': profile_obj.canvas_fingerprint,
                'webgl_fingerprint': profile_obj.webgl_fingerprint,
                'audio_fingerprint': profile_obj.audio_fingerprint,
                'proxy_host': profile_obj.proxy_host,
                'proxy_port': profile_obj.proxy_port,
                'proxy_username': profile_obj.proxy_username,
                'proxy_password': profile_obj.proxy_password,
                'proxy_type': profile_obj.proxy_type or 'http',
            }

        # Get proxy
        pm = ProxyManager()
        pm.load_proxies_from_db()
        proxy_data = None
        if profile_info['proxy_host'] and profile_info['proxy_port']:
            proxy_data = {'host': profile_info['proxy_host'], 'port': profile_info['proxy_port'],
                          'username': profile_info['proxy_username'], 'password': profile_info['proxy_password'],
                          'proxy_type': profile_info['proxy_type']}
        else:
            proxy_data = pm.get_available_proxy()
        if not proxy_data:
            logger.error("No proxy available")
            return results

        logger.info(f"🌐 Batch of {len(domains)} domains | Profile: {profile_info['name']} | Proxy: {proxy_data['host']}:{proxy_data['port']}")

        # Create browser
        browser_manager, browser_id, driver, _profile_dir = _create_browser_from_info(profile_info, proxy_data)

        # Process each domain
        for did in domain_ids:
            domain_name = domains.get(did)
            if not domain_name:
                continue
            try:
                # Navigate to maps search
                maps_url = f"https://yandex.ru/maps/?text={urllib.parse.quote(domain_name)}"
                if not browser_manager.navigate_to_url(browser_id, maps_url, timeout=10):
                    logger.warning(f"⏳ {domain_name}: navigation timeout")
                    _save_domain_result(did, False, None)
                    results.append({"domain": domain_name, "found": False})
                    continue

                # Wait for card (max ~4s)
                time.sleep(1)
                card_loaded = False
                for attempt in range(3):
                    try:
                        cur_url = driver.current_url or ''
                        if 'chrome-error' in cur_url:
                            break
                        for sel in [".card-title-view__title", ".search-business-snippet-view", ".business-card-view"]:
                            els = driver.find_elements(By.CSS_SELECTOR, sel)
                            if els:
                                card_loaded = True
                                break
                        if card_loaded:
                            break
                        # Check "not found" state
                        title = driver.title or ''
                        if 'Яндекс Карты' in title and '/org/' not in (driver.current_url or ''):
                            break
                    except:
                        pass
                    time.sleep(1)

                # Parse
                has_results, parsed, page_title, current_url = _parse_maps_page(driver, domain_name)
                if has_results is None:
                    # chrome-error page
                    logger.warning(f"❌ {domain_name}: page error")
                    _save_domain_result(did, False, None)
                    results.append({"domain": domain_name, "found": False, "error": "page_error"})
                    continue

                _save_domain_result(did, has_results, parsed)
                if has_results:
                    logger.info(f"💾 {domain_name}: {parsed.get('category')} | {parsed.get('city')} | ⭐{parsed.get('rating')}")
                else:
                    logger.info(f"— {domain_name}: not found")
                results.append({"domain": domain_name, "found": has_results})

            except Exception as e:
                logger.error(f"❌ {domain_name}: {e}")
                try:
                    _save_domain_result(did, False, None)
                except:
                    pass
                results.append({"domain": domain_name, "found": False, "error": str(e)})

        return results

    except Exception as e:
        logger.error(f"❌ Batch error: {e}", exc_info=True)
        # Do NOT mark remaining domains as checked — they weren't actually checked
        return results

    finally:
        if browser_manager and browser_id:
            try:
                browser_manager.close_browser_session(browser_id)
            except:
                pass
        elif _profile_dir:
            try:
                bm = BrowserManager.__new__(BrowserManager)
                if hasattr(bm, '_kill_chrome_by_profile_dir'):
                    bm._kill_chrome_by_profile_dir(_profile_dir)
            except:
                pass


# ===== CELERY TASKS =====

@shared_task(bind=True, max_retries=0, time_limit=300, soft_time_limit=280)
def check_batch_task(self, domain_ids: list):
    """Celery task: check a batch of 5 domains with 1 browser session."""
    logger.info(f"🚀 check_batch_task: {len(domain_ids)} domains")
    return check_batch(domain_ids)


@shared_task(bind=True, max_retries=0, time_limit=120, soft_time_limit=100)
def dispatch_domains_task(self):
    """Dispatch batches in waves, self-chaining until all domains are checked.
    
    Each wave dispatches WAVE_SIZE batches (200 domains).
    After dispatching, schedules itself again after WAVE_INTERVAL seconds.
    Stops when: no unchecked domains remain OR Redis flag cleared (manual stop).
    """
    # Check if stopped
    if not is_checking_running():
        logger.info("🛑 Domain checking stopped (flag cleared)")
        return {"status": "stopped"}

    with get_db_session() as db:
        domain_ids = [d.id for d in db.query(DropDomain.id).filter(
            DropDomain.maps_checked == False
        ).order_by(
            DropDomain.hotness.desc(), DropDomain.yandex_tic.desc()
        ).limit(WAVE_SIZE * DOMAINS_PER_BROWSER).all()]

    if not domain_ids:
        logger.info("✅ All domains checked! Stopping.")
        stop_checking()
        return {"status": "done", "dispatched": 0}

    # Split into batches of DOMAINS_PER_BROWSER
    batches = [domain_ids[i:i + DOMAINS_PER_BROWSER] for i in range(0, len(domain_ids), DOMAINS_PER_BROWSER)]
    for batch in batches:
        check_batch_task.delay(batch)

    # Schedule next wave
    dispatch_domains_task.apply_async(countdown=WAVE_INTERVAL)

    logger.info(f"✅ Wave: {len(batches)} batches ({len(domain_ids)} domains), next wave in {WAVE_INTERVAL}s")
    return {"status": "running", "dispatched": len(domain_ids), "batches": len(batches)}


def start_checking():
    """Start the self-chaining domain check process."""
    r = _get_redis()
    r.set(REDIS_RUNNING_KEY, "1")
    dispatch_domains_task.delay()


# Keep backward compat
@shared_task(bind=True, max_retries=1, time_limit=300, soft_time_limit=270)
def check_domain_task(self, domain_id: int, profile_id: int = None):
    """Legacy single-domain task — wraps check_batch."""
    return check_batch([domain_id])


@shared_task(bind=True, max_retries=0, time_limit=300, soft_time_limit=280)
def check_domains_batch_task(self, limit: int = 500):
    """Legacy dispatcher — starts self-chaining process."""
    start_checking()
    return {"status": "started"}
