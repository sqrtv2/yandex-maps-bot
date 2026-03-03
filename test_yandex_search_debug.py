#!/usr/bin/env python3
"""
Debug test: Yandex Search with visible browser + proxy + full captcha logging.
Runs the search flow DIRECTLY (no Celery) so we can watch in real time.
"""
import os
import sys
import time
import random
import logging

# ── Force DEBUG logging on everything ───────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/test_search_debug.log", mode="w"),
    ],
)
# Make key modules verbose
for mod in [
    "core.browser_manager",
    "core.capsola_solver",
    "core.captcha_solver",
    "core.proxy_manager",
    "tasks.yandex_search",
    "tasks.yandex_maps",
    "selenium",
    "urllib3",
]:
    logging.getLogger(mod).setLevel(logging.DEBUG)

logger = logging.getLogger("test_search_debug")

# ── Ensure headless=False (visible browser) ──────────────────────
os.environ["YANDEX_BOT_BROWSER_HEADLESS"] = "false"
os.environ["YANDEX_BOT_DEBUG"] = "true"
os.environ["YANDEX_BOT_LOG_LEVEL"] = "DEBUG"

from app.config import settings
from app.database import get_db_session
from app.models import BrowserProfile
from app.models.proxy import ProxyServer
from app.models.yandex_search_target import YandexSearchTarget
from core.browser_manager import BrowserManager
from core.proxy_manager import ProxyManager
from core.captcha_solver import CaptchaSolver
from core.capsola_solver import create_capsola_solver
from core.profile_generator import ProfileGenerator

# Override headless at runtime
settings.browser_headless = False

def main():
    browser_manager = None
    browser_id = None

    try:
        # ── 1. Load target & profile ────────────────────────────────
        with get_db_session() as db:
            target = db.query(YandexSearchTarget).first()
            profile_obj = db.query(BrowserProfile).filter(
                BrowserProfile.is_active == True
            ).first()
            proxy_obj = db.query(ProxyServer).filter(
                ProxyServer.is_active == True,
                ProxyServer.is_working == True,
            ).first()

        if not target:
            logger.error("❌ No search target in DB"); return
        if not profile_obj:
            logger.error("❌ No active profile in DB"); return

        keyword = target.keywords.strip().split("\n")[0].strip()
        domain = target.domain
        logger.info(f"🎯 Target: domain={domain}, keyword='{keyword}'")
        logger.info(f"👤 Profile: #{profile_obj.id} {profile_obj.name}")

        # ── 2. Proxy ────────────────────────────────────────────────
        proxy_data = None
        if proxy_obj:
            proxy_data = {
                "host": proxy_obj.host,
                "port": proxy_obj.port,
                "username": proxy_obj.username,
                "password": proxy_obj.password,
                "proxy_type": proxy_obj.proxy_type or "http",
            }
            logger.info(f"🌐 Proxy: {proxy_obj.host}:{proxy_obj.port} ({proxy_obj.proxy_type})")
        else:
            logger.warning("⚠️ No proxy — going direct")

        # ── 3. Build profile data ───────────────────────────────────
        profile_generator = ProfileGenerator()
        profile_data = profile_generator.generate_profile(profile_obj.name)
        profile_data.update({
            "user_agent": profile_obj.user_agent,
            "viewport": {
                "width": profile_obj.viewport_width or 1366,
                "height": profile_obj.viewport_height or 768,
            },
            "timezone": profile_obj.timezone or "Europe/Moscow",
            "language": "ru-RU",
        })

        # ── 4. Launch browser ───────────────────────────────────────
        browser_manager = BrowserManager()
        logger.info("🚀 Creating browser session (VISIBLE)...")
        browser_id = browser_manager.create_browser_session(profile_data, proxy_data)
        driver = browser_manager.active_browsers[browser_id]
        logger.info(f"✅ Browser launched: {browser_id}")

        # ── 5. Open ya.ru ───────────────────────────────────────────
        logger.info("🌐 Opening ya.ru …")
        driver.get("https://ya.ru")
        time.sleep(random.uniform(3, 5))

        url = driver.current_url
        title = driver.title
        logger.info(f"📋 URL: {url}")
        logger.info(f"📋 Title: {title}")

        # Save screenshot
        driver.save_screenshot("screenshots/test_debug_01_yaru.png")
        with open("screenshots/test_debug_01_yaru.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)

        # ── 6. Detect captcha ───────────────────────────────────────
        from tasks.yandex_maps import detect_captcha_or_block, handle_yandex_protection

        page_src = driver.page_source[:5000].lower()
        indicators = {
            "showcaptcha_url": "showcaptcha" in url.lower(),
            "captcha_url": "/captcha" in url.lower(),
            "checkbox": "checkboxcaptcha" in page_src,
            "advanced": "advancedcaptcha" in page_src,
            "silhouette": "silhouette" in page_src,
            "kaleidoscope": "kaleidoscope" in page_src,
            "smartcaptcha": "smartcaptcha" in page_src,
            "ya_ne_robot": "я не робот" in page_src,
        }
        found = [k for k, v in indicators.items() if v]
        if found:
            logger.warning(f"🚨 CAPTCHA DETECTED: {found}")
        else:
            logger.info("✅ No captcha on ya.ru homepage")

        has_captcha = detect_captcha_or_block(driver)
        logger.info(f"🔍 detect_captcha_or_block() = {has_captcha}")

        if has_captcha:
            logger.info("=" * 60)
            logger.info("  ATTEMPTING TO SOLVE CAPTCHA")
            logger.info("=" * 60)

            captcha_solver = CaptchaSolver()
            capsola = create_capsola_solver()
            logger.info(f"🔑 Capsola API key: {settings.capsola_api_key[:12]}...")
            logger.info(f"🔑 Capsola enabled: {settings.capsola_enabled}")

            for attempt in range(1, 4):
                logger.info(f"--- Captcha solve attempt {attempt}/3 ---")
                t0 = time.time()
                solved = handle_yandex_protection(driver, captcha_solver)
                elapsed = time.time() - t0
                logger.info(f"Result: solved={solved}, time={elapsed:.1f}s")
                logger.info(f"URL after solve: {driver.current_url}")
                logger.info(f"Title after solve: {driver.title}")

                driver.save_screenshot(f"screenshots/test_debug_captcha_attempt{attempt}.png")

                if solved:
                    logger.info(f"✅ Captcha SOLVED on attempt {attempt}!")
                    break

                if not detect_captcha_or_block(driver):
                    logger.info("✅ Captcha disappeared!")
                    break

                logger.warning(f"❌ Attempt {attempt} failed, refreshing…")
                driver.refresh()
                time.sleep(random.uniform(3, 5))
        else:
            logger.info("No captcha on homepage — proceeding to search")

        # ── 7. Enter keyword and search ─────────────────────────────
        logger.info(f"⌨️ Typing keyword: '{keyword}'")
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.common.action_chains import ActionChains

        search_input = None
        input_selectors = [
            "input.search3__input",
            "input.mini-suggest__input",
            "input.HeaderDesktopForm-Input",
            "input.input__control",
            "input#text",
            "input[name='text']",
            "input[role='searchbox']",
            "input[role='combobox']",
            "form input[type='text']",
            "form input[type='search']",
        ]
        for sel in input_selectors:
            try:
                elems = driver.find_elements(By.CSS_SELECTOR, sel)
                for e in elems:
                    if e.is_displayed() and e.is_enabled():
                        search_input = e
                        logger.info(f"✅ Found search input: {sel}")
                        break
            except:
                continue
            if search_input:
                break

        if not search_input:
            logger.error("❌ Cannot find search input!")
            driver.save_screenshot("screenshots/test_debug_no_input.png")
            with open("screenshots/test_debug_no_input.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
            input("⏸️  Press Enter to close browser...")
            return

        ActionChains(driver).move_to_element(search_input).pause(0.5).click().perform()
        time.sleep(0.5)
        search_input.clear()
        for ch in keyword:
            search_input.send_keys(ch)
            time.sleep(random.uniform(0.05, 0.15))
        time.sleep(1.5)
        search_input.send_keys(Keys.RETURN)
        time.sleep(random.uniform(4, 6))

        logger.info(f"📋 Search results URL: {driver.current_url}")
        logger.info(f"📋 Search results Title: {driver.title}")
        driver.save_screenshot("screenshots/test_debug_02_search_results.png")

        # ── 8. Check captcha on search results ──────────────────────
        has_captcha2 = detect_captcha_or_block(driver)
        if has_captcha2:
            logger.warning("🚨 CAPTCHA on search results page!")

            url2 = driver.current_url.lower()
            src2 = driver.page_source[:5000].lower()
            ind2 = {
                "showcaptcha": "showcaptcha" in url2,
                "checkbox": "checkboxcaptcha" in src2,
                "advanced": "advancedcaptcha" in src2,
                "silhouette": "silhouette" in src2,
                "kaleidoscope": "kaleidoscope" in src2,
                "smartcaptcha": "smartcaptcha" in src2,
            }
            found2 = [k for k, v in ind2.items() if v]
            logger.warning(f"🔍 Search captcha type: {found2}")

            with open("screenshots/test_debug_02_captcha.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)

            captcha_solver = CaptchaSolver()
            for attempt in range(1, 4):
                logger.info(f"--- Search captcha solve attempt {attempt}/3 ---")
                t0 = time.time()
                solved2 = handle_yandex_protection(driver, captcha_solver)
                elapsed2 = time.time() - t0
                logger.info(f"Result: solved={solved2}, time={elapsed2:.1f}s")
                logger.info(f"URL: {driver.current_url}")
                driver.save_screenshot(f"screenshots/test_debug_search_captcha_{attempt}.png")

                if solved2 or not detect_captcha_or_block(driver):
                    logger.info("✅ Search captcha resolved!")
                    break

                logger.warning(f"❌ Attempt {attempt} failed")
                driver.refresh()
                time.sleep(random.uniform(3, 5))
        else:
            logger.info("✅ No captcha on search results")

        # ── 9. Look for target domain ───────────────────────────────
        logger.info(f"🔎 Looking for {domain} in search results...")
        links = driver.find_elements(By.CSS_SELECTOR, "a[href]")
        domain_clean = domain.lower().replace("www.", "")
        found_links = []
        for i, link in enumerate(links):
            try:
                href = link.get_attribute("href") or ""
                if domain_clean in href.lower():
                    text = link.text.strip()[:60]
                    found_links.append((i, href, text))
                    logger.info(f"  ✅ FOUND at #{i}: '{text}' → {href[:100]}")
            except:
                continue

        if found_links:
            logger.info(f"🎯 Found {len(found_links)} links to {domain}!")
        else:
            logger.warning(f"❌ Domain {domain} NOT found in search results")

        # ── 10. Keep browser open for inspection ────────────────────
        logger.info("=" * 60)
        logger.info("  BROWSER IS OPEN — inspect manually")
        logger.info("  Press Enter in terminal to close")
        logger.info("=" * 60)
        input("⏸️  Press Enter to close browser...")

    except Exception as e:
        logger.error(f"❌ Error: {e}", exc_info=True)
        input("⏸️  Press Enter to close browser...")

    finally:
        if browser_manager and browser_id:
            try:
                browser_manager.close_browser_session(browser_id)
                logger.info("🔒 Browser closed")
            except Exception as e:
                logger.warning(f"Close error: {e}")


if __name__ == "__main__":
    main()
