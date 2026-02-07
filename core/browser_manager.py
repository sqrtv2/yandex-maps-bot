"""
Browser manager for automated browser sessions with profile management.
"""
import os
import time
import random
import json
import logging
import subprocess
import signal
import tempfile
import zipfile
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, WebDriverException,
    ElementClickInterceptedException, StaleElementReferenceException
)
import undetected_chromedriver as uc
from webdriver_manager.chrome import ChromeDriverManager

from app.config import settings
from .profile_generator import ProfileGenerator

logger = logging.getLogger(__name__)


class BrowserManager:
    """Manages browser instances with profiles and automation."""

    def __init__(self):
        self.profile_generator = ProfileGenerator()
        self.active_browsers = {}  # browser_id -> browser_instance
        self.browser_profiles = {}  # browser_id -> profile_data
        self.driver_path = None
        self._setup_driver()

    def _setup_driver(self):
        """Setup Chrome driver."""
        try:
            if settings.browser_binary_path and os.path.exists(settings.browser_binary_path):
                self.driver_path = settings.browser_binary_path
            else:
                # Use webdriver manager to download driver
                self.driver_path = ChromeDriverManager().install()
            logger.info(f"Chrome driver setup: {self.driver_path}")
        except Exception as e:
            logger.error(f"Error setting up driver: {e}")

    def _create_proxy_auth_extension(self, proxy_data: Dict) -> str:
        """Create a Chrome extension for proxy authentication.
        
        Returns path to the extension zip file.
        This replaces selenium-wire MITM approach — no more 'Not Secure' warnings
        and no 'data:,' initial page.
        """
        host = proxy_data['host']
        port = proxy_data['port']
        username = proxy_data['username']
        password = proxy_data['password']
        proxy_type = proxy_data.get('proxy_type', 'http').lower()
        
        # Определяем scheme для chrome.proxy API
        # Chrome поддерживает: "http", "https", "socks4", "socks5"
        if proxy_type in ('socks5', 'socks'):
            scheme = 'socks5'
        elif proxy_type == 'socks4':
            scheme = 'socks4'
        elif proxy_type == 'https':
            scheme = 'https'
        else:
            scheme = 'http'

        manifest_json = json.dumps({
            "version": "1.0.0",
            "manifest_version": 2,
            "name": "Chrome Proxy Auth",
            "permissions": [
                "proxy",
                "tabs",
                "unlimitedStorage",
                "storage",
                "<all_urls>",
                "webRequest",
                "webRequestBlocking"
            ],
            "background": {
                "scripts": ["background.js"]
            },
            "minimum_chrome_version": "76.0.0"
        })

        background_js = """var config = {
    mode: "fixed_servers",
    rules: {
        singleProxy: {
            scheme: "%s",
            host: "%s",
            port: parseInt(%s)
        },
        bypassList: ["localhost", "127.0.0.1"]
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

        # Create temp directory for extension
        ext_dir = tempfile.mkdtemp(prefix='proxy_auth_')
        ext_path = os.path.join(ext_dir, 'proxy_auth.zip')

        with zipfile.ZipFile(ext_path, 'w') as zf:
            zf.writestr('manifest.json', manifest_json)
            zf.writestr('background.js', background_js)

        logger.info(f"📦 Created proxy auth extension: {ext_path}")
        return ext_path

    def create_browser_session(self, profile_data: Dict, proxy_data: Optional[Dict] = None) -> str:
        """Create a new browser session with specified profile."""
        proxy_ext_path = None
        try:
            # Add random delay to prevent race conditions when creating multiple browsers
            time.sleep(random.uniform(1.0, 3.0))
            
            browser_id = f"browser_{int(time.time())}_{random.randint(1000, 9999)}"

            # If proxy has auth, create Chrome extension for it (replaces selenium-wire)
            if proxy_data and proxy_data.get('username') and proxy_data.get('password'):
                proxy_ext_path = self._create_proxy_auth_extension(proxy_data)
                logger.info(f"🔐 Proxy auth via Chrome extension (no MITM, no 'Not Secure')")

            # Setup Chrome options
            chrome_options = self._create_chrome_options(profile_data, proxy_data, proxy_ext_path)

            # Always use undetected-chromedriver (no selenium-wire needed)
            logger.info("Creating browser with undetected-chromedriver")
            if settings.debug:
                driver = uc.Chrome(
                    options=chrome_options,
                    service_args=["--verbose"],
                    version_main=144
                )
            else:
                driver = uc.Chrome(options=chrome_options, version_main=144)

            # Apply profile settings
            self._apply_profile_settings(driver, profile_data)

            # Store browser instance
            self.active_browsers[browser_id] = driver
            self.browser_profiles[browser_id] = profile_data

            logger.info(f"Created browser session: {browser_id}")
            return browser_id

        except Exception as e:
            logger.error(f"Error creating browser session: {e}")
            raise
        finally:
            # Cleanup temp extension file
            if proxy_ext_path and os.path.exists(proxy_ext_path):
                try:
                    import shutil
                    shutil.rmtree(os.path.dirname(proxy_ext_path), ignore_errors=True)
                except Exception:
                    pass

    def _create_chrome_options(self, profile_data: Dict, proxy_data: Optional[Dict] = None, proxy_ext_path: Optional[str] = None) -> Options:
        """Create Chrome options based on profile data."""
        options = Options()

        # Basic settings
        if settings.browser_headless:
            options.add_argument("--headless=new")

        # Profile directory — ВСЕГДА используем сохранённый профиль с куками
        profile_dir = os.path.join(settings.browser_user_data_dir, profile_data["name"])
        options.add_argument(f"--user-data-dir={profile_dir}")
        logger.info(f"📁 Using saved profile with cookies: {profile_dir}")

        # Window size
        viewport = profile_data.get("viewport", {"width": 1366, "height": 768})
        options.add_argument(f"--window-size={viewport['width']},{viewport['height']}")

        # User agent
        if "user_agent" in profile_data:
            options.add_argument(f"--user-agent={profile_data['user_agent']}")

        # Language
        if "language" in profile_data:
            options.add_argument(f"--lang={profile_data['language']}")

        # Timezone
        if "timezone" in profile_data:
            options.add_argument(f"--timezone={profile_data['timezone']}")

        # Proxy settings
        if proxy_ext_path:
            # Прокси с авторизацией — extension управляет всем, --proxy-server НЕ нужен
            logger.info(f"✅ Proxy handled entirely by Chrome extension")
        elif proxy_data and not (proxy_data.get('username') and proxy_data.get('password')):
            # Прокси без авторизации — через аргумент Chrome
            proxy_type = proxy_data.get('proxy_type', 'http')
            proxy_url = f"{proxy_type}://{proxy_data['host']}:{proxy_data['port']}"
            logger.info(f"✅ Using proxy without auth: {proxy_url}")
            options.add_argument(f"--proxy-server={proxy_url}")

        # Load proxy auth extension if provided
        if proxy_ext_path:
            options.add_extension(proxy_ext_path)
            logger.info(f"📦 Loaded proxy auth extension")

        # Anti-detection flags
        stealth_flags = profile_data.get("chrome_flags", [])
        for flag in stealth_flags:
            options.add_argument(flag)

        # Additional anti-detection settings
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-features=VizDisplayCompositor")
        options.add_argument("--disable-blink-features=AutomationControlled")

        # Prefs
        prefs = {
            "download.default_directory": settings.browser_download_dir,
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True
        }
        if "language" in profile_data:
            prefs["intl.accept_languages"] = profile_data['language']

        # Disable images to speed up (optional)
        if not profile_data.get("images_enabled", True):
            prefs["profile.managed_default_content_settings.images"] = 2

        options.add_experimental_option("prefs", prefs)
        # НЕ добавляем excludeSwitches и useAutomationExtension —
        # undetected_chromedriver обрабатывает анти-детекцию автоматически

        return options

    def _apply_profile_settings(self, driver: webdriver.Chrome, profile_data: Dict):
        """Apply JavaScript-based profile settings to browser."""
        try:
            # Set viewport size
            viewport = profile_data.get("viewport", {})
            if viewport:
                driver.set_window_size(viewport.get("width", 1366), viewport.get("height", 768))

            # Inject fingerprinting scripts
            self._inject_fingerprint_scripts(driver, profile_data)

            # Set timezone via CDP if available
            if hasattr(driver, 'execute_cdp_cmd'):
                try:
                    driver.execute_cdp_cmd('Emulation.setTimezoneOverride', {
                        'timezoneId': profile_data.get('timezone', 'Europe/Moscow')
                    })
                except Exception as e:
                    logger.warning(f"Could not set timezone via CDP: {e}")

            logger.info(f"Applied profile settings for: {profile_data['name']}")

        except Exception as e:
            logger.error(f"Error applying profile settings: {e}")

    def _inject_fingerprint_scripts(self, driver: webdriver.Chrome, profile_data: Dict):
        """Inject JavaScript to modify browser fingerprints."""
        try:
            # Canvas fingerprint modification
            canvas_script = f"""
            const originalGetContext = HTMLCanvasElement.prototype.getContext;
            HTMLCanvasElement.prototype.getContext = function(type) {{
                const context = originalGetContext.call(this, type);
                if (type === '2d') {{
                    const originalFillText = context.fillText;
                    context.fillText = function(text, x, y, maxWidth) {{
                        // Add slight noise to canvas rendering
                        const noise = {random.random() * 0.001};
                        return originalFillText.call(this, text, x + noise, y + noise, maxWidth);
                    }};
                }}
                return context;
            }};
            """

            # WebGL fingerprint modification
            webgl_data = profile_data.get("webgl_fingerprint", {})
            webgl_script = f"""
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {{
                if (parameter === 37445) {{
                    return '{webgl_data.get("vendor", "Google Inc.")}';
                }}
                if (parameter === 37446) {{
                    return '{webgl_data.get("renderer", "ANGLE")}';
                }}
                return getParameter.call(this, parameter);
            }};
            """

            # Hardware concurrency override
            hardware_script = f"""
            Object.defineProperty(navigator, 'hardwareConcurrency', {{
                get: () => {profile_data.get('hardware_concurrency', 4)}
            }});
            """

            # Device memory override
            memory_script = f"""
            Object.defineProperty(navigator, 'deviceMemory', {{
                get: () => {profile_data.get('device_memory', 8)}
            }});
            """

            # Platform override
            platform_script = f"""
            Object.defineProperty(navigator, 'platform', {{
                get: () => '{profile_data.get("platform", "Win32")}'
            }});
            """

            # Language override
            language_script = f"""
            Object.defineProperty(navigator, 'language', {{
                get: () => '{profile_data.get("language", "en-US")}'
            }});
            Object.defineProperty(navigator, 'languages', {{
                get: () => ['{profile_data.get("language", "en-US")}']
            }});
            """

            # Execute all scripts
            scripts = [
                canvas_script, webgl_script, hardware_script,
                memory_script, platform_script, language_script
            ]

            for script in scripts:
                try:
                    driver.execute_script(script)
                except Exception as e:
                    logger.warning(f"Error executing fingerprint script: {e}")

        except Exception as e:
            logger.error(f"Error injecting fingerprint scripts: {e}")

    def navigate_to_url(self, browser_id: str, url: str, timeout: int = 30) -> bool:
        """Navigate browser to specified URL."""
        try:
            if browser_id not in self.active_browsers:
                raise ValueError(f"Browser session {browser_id} not found")

            driver = self.active_browsers[browser_id]
            driver.set_page_load_timeout(timeout)
            driver.get(url)

            # Wait for page to load
            WebDriverWait(driver, timeout).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )

            logger.info(f"Successfully navigated {browser_id} to {url}")
            return True

        except TimeoutException:
            logger.error(f"Timeout navigating {browser_id} to {url}")
            return False
        except Exception as e:
            logger.error(f"Error navigating {browser_id} to {url}: {e}")
            return False

    def perform_human_actions(self, browser_id: str, actions: List[str] = None) -> bool:
        """Perform human-like actions on the current page."""
        try:
            if browser_id not in self.active_browsers:
                raise ValueError(f"Browser session {browser_id} not found")

            driver = self.active_browsers[browser_id]
            action_chain = ActionChains(driver)

            # Default actions if none specified
            if not actions:
                actions = ["scroll", "mouse_move", "click_random"]

            for action in actions:
                try:
                    if action == "scroll":
                        self._perform_scroll(driver)
                    elif action == "mouse_move":
                        self._perform_mouse_movement(driver, action_chain)
                    elif action == "click_random":
                        self._perform_random_click(driver)
                    elif action == "type_text":
                        self._perform_typing(driver)

                    # Random delay between actions
                    time.sleep(random.uniform(1, 3))

                except Exception as e:
                    logger.warning(f"Error performing action {action}: {e}")
                    continue

            return True

        except Exception as e:
            logger.error(f"Error performing human actions in {browser_id}: {e}")
            return False

    def _perform_scroll(self, driver: webdriver.Chrome):
        """Perform realistic scrolling (limited to ~10 seconds max)."""
        # Get page height
        total_height = driver.execute_script("return document.body.scrollHeight")
        viewport_height = driver.execute_script("return window.innerHeight")

        # Limit scrolling: max 8 scroll actions or 10 seconds
        current_position = 0
        max_scrolls = random.randint(3, 8)
        scroll_start = time.time()

        for _ in range(max_scrolls):
            if time.time() - scroll_start > 10:
                break
            if current_position >= total_height - viewport_height:
                break

            # Random scroll distance
            scroll_distance = random.randint(200, 600)
            current_position += scroll_distance

            driver.execute_script(f"window.scrollTo(0, {current_position});")

            # Random pause
            time.sleep(random.uniform(0.3, 1.0))

            # Sometimes scroll back up a bit
            if random.random() < 0.15:
                back_scroll = random.randint(50, 200)
                current_position = max(0, current_position - back_scroll)
                driver.execute_script(f"window.scrollTo(0, {current_position});")

    def _perform_mouse_movement(self, driver: webdriver.Chrome, action_chain: ActionChains):
        """Perform realistic mouse movements."""
        viewport_width = driver.execute_script("return window.innerWidth")
        viewport_height = driver.execute_script("return window.innerHeight")

        # Move to random positions
        for _ in range(random.randint(2, 5)):
            x = random.randint(0, viewport_width)
            y = random.randint(0, viewport_height)

            action_chain.move_by_offset(x, y).perform()
            time.sleep(random.uniform(0.1, 0.5))

    def _perform_random_click(self, driver: webdriver.Chrome):
        """Click on random safe elements."""
        try:
            # Find clickable elements
            clickable_elements = driver.find_elements(
                By.CSS_SELECTOR,
                "a, button, div[onclick], span[onclick], [role='button']"
            )

            if clickable_elements:
                # Filter out potentially dangerous elements
                safe_elements = []
                for element in clickable_elements:
                    try:
                        if element.is_displayed() and element.is_enabled():
                            # Avoid elements with dangerous attributes
                            onclick = element.get_attribute("onclick") or ""
                            href = element.get_attribute("href") or ""

                            if not any(danger in onclick.lower() for danger in ["submit", "delete", "remove"]):
                                if not href.startswith("javascript:"):
                                    safe_elements.append(element)
                    except:
                        continue

                if safe_elements:
                    element = random.choice(safe_elements)
                    try:
                        driver.execute_script("arguments[0].click();", element)
                        time.sleep(random.uniform(1, 2))
                    except:
                        pass

        except Exception as e:
            logger.warning(f"Error performing random click: {e}")

    def _perform_typing(self, driver: webdriver.Chrome):
        """Type in input fields if available."""
        try:
            input_elements = driver.find_elements(
                By.CSS_SELECTOR,
                "input[type='text'], input[type='search'], textarea"
            )

            safe_inputs = []
            for element in input_elements:
                try:
                    if element.is_displayed() and element.is_enabled():
                        # Avoid password and email fields
                        input_type = element.get_attribute("type") or ""
                        name = element.get_attribute("name") or ""
                        if input_type not in ["password", "email"] and "password" not in name.lower():
                            safe_inputs.append(element)
                except:
                    continue

            if safe_inputs:
                element = random.choice(safe_inputs)
                try:
                    # Type random text
                    texts = ["test", "hello", "search", "example", "demo"]
                    text = random.choice(texts)

                    element.clear()
                    for char in text:
                        element.send_keys(char)
                        time.sleep(random.uniform(0.1, 0.3))

                    time.sleep(random.uniform(1, 2))
                    element.send_keys(Keys.ESCAPE)  # Close any dropdowns
                except:
                    pass

        except Exception as e:
            logger.warning(f"Error performing typing: {e}")

    def wait_random_time(self, min_seconds: int = 30, max_seconds: int = 300):
        """Wait for random time to simulate human behavior."""
        wait_time = random.randint(min_seconds, max_seconds)
        logger.info(f"Waiting {wait_time} seconds...")
        time.sleep(wait_time)

    def take_screenshot(self, browser_id: str, filename: Optional[str] = None) -> Optional[str]:
        """Take screenshot of current page."""
        try:
            if browser_id not in self.active_browsers:
                raise ValueError(f"Browser session {browser_id} not found")

            driver = self.active_browsers[browser_id]

            if not filename:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"screenshot_{browser_id}_{timestamp}.png"

            filepath = os.path.join(settings.screenshots_dir, filename)
            os.makedirs(os.path.dirname(filepath), exist_ok=True)

            driver.save_screenshot(filepath)
            logger.info(f"Screenshot saved: {filepath}")
            return filepath

        except Exception as e:
            logger.error(f"Error taking screenshot for {browser_id}: {e}")
            return None

    def close_browser_session(self, browser_id: str):
        """Close browser session and cleanup."""
        driver = None
        try:
            if browser_id in self.active_browsers:
                driver = self.active_browsers[browser_id]
                
                # Сохраняем PID процесса ChromeDriver до закрытия
                chromedriver_pid = None
                try:
                    if hasattr(driver, 'service') and hasattr(driver.service, 'process'):
                        chromedriver_pid = driver.service.process.pid
                except:
                    pass
                
                # Закрываем браузер через Selenium
                try:
                    driver.quit()
                except Exception as quit_error:
                    logger.warning(f"Error during driver.quit() for {browser_id}: {quit_error}")
                
                # Принудительно убиваем процесс ChromeDriver если он всё ещё жив
                if chromedriver_pid:
                    try:
                        os.kill(chromedriver_pid, signal.SIGTERM)
                        time.sleep(0.5)
                        # Если всё ещё жив, используем SIGKILL
                        try:
                            os.kill(chromedriver_pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass  # Процесс уже завершён
                    except ProcessLookupError:
                        pass  # Процесс уже завершён
                    except Exception as kill_error:
                        logger.warning(f"Could not kill ChromeDriver PID {chromedriver_pid}: {kill_error}")
                
                del self.active_browsers[browser_id]

            if browser_id in self.browser_profiles:
                del self.browser_profiles[browser_id]

            logger.info(f"Closed browser session: {browser_id}")

        except Exception as e:
            logger.error(f"Error closing browser session {browser_id}: {e}")
            # В случае ошибки всё равно удаляем из словарей
            if browser_id in self.active_browsers:
                del self.active_browsers[browser_id]
            if browser_id in self.browser_profiles:
                del self.browser_profiles[browser_id]

    def close_all_sessions(self):
        """Close all active browser sessions."""
        browser_ids = list(self.active_browsers.keys())
        for browser_id in browser_ids:
            self.close_browser_session(browser_id)

        logger.info("All browser sessions closed")

    def get_active_sessions(self) -> List[str]:
        """Get list of active browser session IDs."""
        return list(self.active_browsers.keys())

    def get_session_info(self, browser_id: str) -> Optional[Dict]:
        """Get information about browser session."""
        if browser_id not in self.active_browsers:
            return None

        driver = self.active_browsers[browser_id]
        profile = self.browser_profiles.get(browser_id, {})

        try:
            return {
                "browser_id": browser_id,
                "current_url": driver.current_url,
                "title": driver.title,
                "profile_name": profile.get("name", "Unknown"),
                "window_size": driver.get_window_size(),
                "is_alive": True
            }
        except Exception as e:
            logger.warning(f"Error getting session info for {browser_id}: {e}")
            return {
                "browser_id": browser_id,
                "profile_name": profile.get("name", "Unknown"),
                "is_alive": False,
                "error": str(e)
            }