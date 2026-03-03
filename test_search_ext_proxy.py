#!/usr/bin/env python3
"""
Full debug test: Yandex Search with visible browser + proxy (Chrome extension method).
"""
import os, sys, time, random, shutil, logging, zipfile, json

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/test_search_debug.log", mode="w"),
    ],
)
for mod in ["core.browser_manager", "core.capsola_solver", "tasks.yandex_maps", "tasks.yandex_search"]:
    logging.getLogger(mod).setLevel(logging.DEBUG)
logger = logging.getLogger("test_search_debug")

os.environ["YANDEX_BOT_BROWSER_HEADLESS"] = "false"
os.environ["YANDEX_BOT_DEBUG"] = "true"

sys.path.insert(0, os.path.dirname(__file__))


def create_proxy_extension(host, port, username, password, scheme="http"):
    """Create Chrome extension for proxy authentication."""
    manifest = {
        "version": "1.0.0",
        "manifest_version": 2,
        "name": "Proxy Auth",
        "permissions": ["proxy", "tabs", "unlimitedStorage", "storage",
                        "<all_urls>", "webRequest", "webRequestBlocking"],
        "background": {"scripts": ["background.js"]},
        "minimum_chrome_version": "22.0.0"
    }
    background_js = """
    var config = {
        mode: "fixed_servers",
        rules: {
            singleProxy: {
                scheme: "%s",
                host: "%s",
                port: parseInt(%s)
            },
            bypassList: ["localhost"]
        }
    };
    chrome.proxy.settings.set({value: config, scope: "regular"}, function() {});
    function callbackFn(details) {
        return {
            authCredentials: {
                username: "%s",
                password: "%s"
            }
        };
    }
    chrome.webRequest.onAuthRequired.addListener(
        callbackFn,
        {urls: ["<all_urls>"]},
        ['blocking']
    );
    """ % (scheme, host, port, username, password)

    ext_dir = os.path.join(os.path.dirname(__file__), 'proxy_ext')
    os.makedirs(ext_dir, exist_ok=True)
    ext_path = os.path.join(ext_dir, 'proxy_auth.zip')
    with zipfile.ZipFile(ext_path, 'w') as zp:
        zp.writestr("manifest.json", json.dumps(manifest))
        zp.writestr("background.js", background_js)
    return ext_path


def main():
    import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.common.action_chains import ActionChains

    profile_dir = os.path.abspath('./browser_profiles/Profile-Test-Debug')
    if os.path.exists(profile_dir):
        shutil.rmtree(profile_dir)

    # Create proxy extension
    proxy_ext = create_proxy_extension(
        host="mproxy.site", port=12138,
        username="Hes9yF", password="zAU2vaEUf4TU",
        scheme="http"
    )
    logger.info(f"Proxy extension: {proxy_ext}")

    options = uc.ChromeOptions()
    options.add_argument('--no-sandbox')
    options.add_argument('--window-size=1366,768')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--lang=ru-RU')
    options.add_argument('--accept-lang=ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_extension(proxy_ext)

    logger.info("🚀 Launching Chrome with proxy extension...")
    driver = uc.Chrome(
        options=options,
        user_data_dir=profile_dir,
        version_main=144
    )
    logger.info("✅ Chrome launched!")

    try:
        # ── Step 1: Open ya.ru ──
        logger.info("🌐 Navigating to ya.ru...")
        driver.get("https://ya.ru")
        time.sleep(random.uniform(4, 6))

        url = driver.current_url
        title = driver.title
        logger.info(f"📋 URL: {url}")
        logger.info(f"📋 Title: {title}")
        driver.save_screenshot("screenshots/debug_01_yaru.png")
        with open("screenshots/debug_01_yaru.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        logger.info("📸 Screenshot & HTML saved")

        # ── Step 2: Check for captcha ──
        from tasks.yandex_maps import detect_captcha_or_block, handle_yandex_protection
        from core.captcha_solver import CaptchaSolver

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
            "dzen": "dzen.ru" in url.lower(),
        }
        found = [k for k, v in indicators.items() if v]
        has_captcha = detect_captcha_or_block(driver)

        if found:
            logger.warning(f"🚨 CAPTCHA indicators: {found}")
        if has_captcha:
            logger.warning(f"🚨 detect_captcha_or_block() = True")
        else:
            logger.info("✅ No captcha on homepage")

        # ── Step 3: Solve captcha if present ──
        if has_captcha:
            logger.info("=" * 60)
            logger.info("  SOLVING CAPTCHA ON HOMEPAGE")
            logger.info("=" * 60)
            solver = CaptchaSolver()
            for attempt in range(1, 4):
                logger.info(f"--- Attempt {attempt}/3 ---")
                t0 = time.time()
                solved = handle_yandex_protection(driver, solver)
                elapsed = time.time() - t0
                logger.info(f"Result: solved={solved}, time={elapsed:.1f}s, URL={driver.current_url[:120]}")
                driver.save_screenshot(f"screenshots/debug_captcha_attempt{attempt}.png")
                if solved or not detect_captcha_or_block(driver):
                    logger.info("✅ Captcha resolved!")
                    break
                logger.warning(f"❌ Attempt {attempt} failed")
                driver.refresh()
                time.sleep(random.uniform(3, 5))

        # ── Step 4: Search for keyword ──
        keyword = "benesque"
        logger.info(f"⌨️ Searching: '{keyword}'")

        search_input = None
        for sel in ["input#text", "input[name='text']", "input.search3__input",
                     "input.mini-suggest__input", "input[role='searchbox']",
                     "input[role='combobox']", "form input[type='text']"]:
            try:
                elems = driver.find_elements(By.CSS_SELECTOR, sel)
                for e in elems:
                    if e.is_displayed() and e.is_enabled():
                        search_input = e
                        logger.info(f"✅ Found input: {sel}")
                        break
            except:
                continue
            if search_input:
                break

        if not search_input:
            logger.error("❌ No search input found!")
            driver.save_screenshot("screenshots/debug_no_input.png")
            with open("screenshots/debug_no_input.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
            input("⏸️  Press Enter to close...")
            return

        ActionChains(driver).move_to_element(search_input).pause(0.5).click().perform()
        time.sleep(0.3)
        search_input.clear()
        for ch in keyword:
            search_input.send_keys(ch)
            time.sleep(random.uniform(0.05, 0.15))
        time.sleep(1.5)
        search_input.send_keys(Keys.RETURN)
        logger.info("⌨️ Search submitted, waiting for results...")
        time.sleep(random.uniform(5, 7))

        logger.info(f"📋 Results URL: {driver.current_url}")
        logger.info(f"📋 Results Title: {driver.title}")
        driver.save_screenshot("screenshots/debug_02_results.png")

        # ── Step 5: Captcha on results? ──
        if detect_captcha_or_block(driver):
            logger.warning("🚨 CAPTCHA on search results!")
            url2 = driver.current_url.lower()
            src2 = driver.page_source[:3000].lower()
            types = []
            if 'showcaptcha' in url2: types.append('showcaptcha')
            if 'checkboxcaptcha' in src2: types.append('checkbox')
            if 'kaleidoscope' in src2: types.append('kaleidoscope')
            if 'silhouette' in src2: types.append('silhouette')
            if 'smartcaptcha' in src2: types.append('smartcaptcha')
            logger.warning(f"🔍 Type: {types}")

            with open("screenshots/debug_results_captcha.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)

            solver = CaptchaSolver()
            for attempt in range(1, 4):
                logger.info(f"--- Results captcha attempt {attempt}/3 ---")
                t0 = time.time()
                solved = handle_yandex_protection(driver, solver)
                elapsed = time.time() - t0
                logger.info(f"Result: solved={solved}, time={elapsed:.1f}s")
                driver.save_screenshot(f"screenshots/debug_results_captcha_{attempt}.png")
                if solved or not detect_captcha_or_block(driver):
                    logger.info("✅ Results captcha resolved!")
                    break
                driver.refresh()
                time.sleep(3)
        else:
            logger.info("✅ No captcha on results page")

        # ── Step 6: Look for target domain ──
        domain = "benesque.ru"
        logger.info(f"🔎 Looking for {domain}...")
        links = driver.find_elements(By.CSS_SELECTOR, "a[href]")
        count = 0
        for link in links:
            try:
                href = (link.get_attribute("href") or "").lower()
                if domain in href:
                    count += 1
                    logger.info(f"  ✅ #{count}: {link.text.strip()[:60]} → {href[:100]}")
            except:
                continue
        if count:
            logger.info(f"🎯 Found {count} links to {domain}")
        else:
            logger.warning(f"❌ {domain} not found")

        # ── Keep browser open for manual inspection ──
        logger.info("=" * 60)
        logger.info("  BROWSER IS OPEN — inspect it manually")
        logger.info("  Press Enter in terminal to close")
        logger.info("=" * 60)
        input("\n⏸️  Press Enter to close browser...")

    except Exception as e:
        logger.error(f"❌ Error: {e}", exc_info=True)
        try:
            driver.save_screenshot("screenshots/debug_error.png")
        except:
            pass
        input("\n⏸️  Press Enter to close browser...")
    finally:
        try:
            driver.quit()
        except:
            pass
        logger.info("🔒 Done!")


if __name__ == "__main__":
    main()
