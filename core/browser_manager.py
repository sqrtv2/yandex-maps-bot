"""
Browser manager for automated browser sessions with profile management.
Uses Playwright (Chromium) for browser automation.
"""
import os
import time
import random
import json
import logging
import subprocess
import signal
import tempfile
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta

from playwright.sync_api import sync_playwright, Playwright, Browser, BrowserContext, Page
from playwright_stealth import Stealth
from .playwright_driver import (
    PlaywrightDriver, PlaywrightElement, PlaywrightActionChains,
    PlaywrightWait, By, Keys, expected_conditions, EC,
    WebDriverException, TimeoutException, NoSuchElementException,
    ElementClickInterceptedException, StaleElementReferenceException,
)

from app.config import settings
from .profile_generator import ProfileGenerator

logger = logging.getLogger(__name__)

# Shared Playwright instance (reused across BrowserManager instances within the same process)
_playwright_instance: Optional[Playwright] = None


def _get_playwright() -> Playwright:
    """Get or create the shared Playwright instance."""
    global _playwright_instance
    if _playwright_instance is None:
        _playwright_instance = sync_playwright().start()
        logger.info("✅ Playwright instance started")
    return _playwright_instance


def _kill_process_tree(pid: int):
    """Kill a process and all its children."""
    try:
        import psutil
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        for child in children:
            try:
                child.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        try:
            parent.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        psutil.wait_procs(children + [parent], timeout=5)
    except ImportError:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    except Exception:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass


def cleanup_orphaned_chrome():
    """Kill only truly orphaned Chrome processes (whose Playwright driver parent is dead).
    
    SAFE: Does NOT kill Chrome processes that are actively used by other workers.
    Only kills Chrome processes whose parent Node.js driver (or Celery worker) no longer exists.
    """
    killed = 0
    try:
        # Find all Chrome processes and check if their parent chain includes a live Celery worker
        for pid_dir in os.listdir('/proc'):
            if not pid_dir.isdigit():
                continue
            pid = int(pid_dir)
            try:
                cmdline_path = f'/proc/{pid}/cmdline'
                with open(cmdline_path, 'r') as f:
                    cmdline = f.read()
                if 'chrom' not in cmdline.lower():
                    continue
                # This is a Chrome process. Check if its parent chain leads to a live process.
                # Walk up the parent tree: chrome → node (playwright driver) → python (celery worker)
                ppid = pid
                is_orphan = True
                for _ in range(5):  # max 5 levels up
                    try:
                        with open(f'/proc/{ppid}/stat', 'r') as f:
                            stat = f.read()
                        ppid = int(stat.split(')')[1].split()[1])  # PPID field
                        if ppid <= 1:
                            # Parent is init/docker-init — this process is orphaned
                            is_orphan = True
                            break
                        # Check if parent is a Celery worker or Node.js driver
                        with open(f'/proc/{ppid}/cmdline', 'r') as f:
                            parent_cmd = f.read()
                        if 'celery' in parent_cmd.lower():
                            is_orphan = False
                            break
                        if 'run-driver' in parent_cmd:
                            # Node.js driver — check its parent too
                            continue
                    except (FileNotFoundError, ProcessLookupError, ValueError, IndexError):
                        # Parent process doesn't exist — orphaned
                        is_orphan = True
                        break
                
                if is_orphan:
                    try:
                        os.kill(pid, signal.SIGKILL)
                        killed += 1
                    except (ProcessLookupError, PermissionError):
                        pass
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue
    except Exception as e:
        logger.warning(f"Error in cleanup_orphaned_chrome: {e}")
    if killed:
        logger.info(f"🧹 Cleaned up {killed} orphaned Chrome processes")
    return killed


def cleanup_all_chrome():
    """Kill ALL Chrome processes in the container. Use only during full worker shutdown."""
    killed = 0
    try:
        result = subprocess.run(
            ['pkill', '-9', '-f', 'chrome.*--no-sandbox'],
            capture_output=True, timeout=5
        )
        count_result = subprocess.run(
            ['sh', '-c', 'pgrep -c chrome 2>/dev/null || echo 0'],
            capture_output=True, text=True, timeout=5
        )
        killed = int(count_result.stdout.strip())
        if killed > 0:
            subprocess.run(['pkill', '-9', 'chrome'], capture_output=True, timeout=5)
    except FileNotFoundError:
        try:
            for pid_dir in os.listdir('/proc'):
                if not pid_dir.isdigit():
                    continue
                try:
                    with open(f'/proc/{pid_dir}/cmdline', 'r') as f:
                        cmdline = f.read()
                    if 'chrom' in cmdline.lower():
                        os.kill(int(pid_dir), signal.SIGKILL)
                        killed += 1
                except (FileNotFoundError, ProcessLookupError, PermissionError):
                    pass
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"Error in cleanup_all_chrome: {e}")
    if killed:
        logger.info(f"🧹 Killed ALL Chrome processes ({killed})")
    return killed



class BrowserManager:
    """Manages browser instances with profiles and automation (Playwright)."""

    def __init__(self):
        self.profile_generator = ProfileGenerator()
        self.active_browsers = {}  # browser_id -> PlaywrightDriver
        self.browser_profiles = {}  # browser_id -> profile_data
        self.browser_pids = {}  # browser_id -> {'chrome_pid': int, 'profile_dir': str}

    @staticmethod
    def repair_profile_dir(profile_dir: str):
        """Repair a Chrome profile directory before launch.
        
        Fixes common corruption caused by Chrome being SIGKILL'd:
        - Bloated Preferences file (can grow to GBs when Chrome is killed mid-write)
        - Stale lock files preventing Chrome from starting
        - Accumulated crash artifacts eating disk space
        """
        if not os.path.isdir(profile_dir):
            return
        
        # 1. Fix bloated Preferences file (normal size is <1MB, corrupted can be GBs)
        prefs_file = os.path.join(profile_dir, 'Default', 'Preferences')
        if os.path.exists(prefs_file):
            try:
                size_mb = os.path.getsize(prefs_file) / (1024 * 1024)
                if size_mb > 5:  # > 5MB is definitely corrupted
                    logger.warning(f"🔧 Repairing bloated Preferences ({size_mb:.0f}MB): {prefs_file}")
                    with open(prefs_file, 'w') as f:
                        f.write('{}')
            except Exception as e:
                logger.warning(f"Could not check/repair Preferences: {e}")
        
        # 2. Remove stale lock files
        for lock_file in ['SingletonLock', 'SingletonSocket', 'SingletonCookie']:
            lock_path = os.path.join(profile_dir, lock_file)
            if os.path.exists(lock_path) or os.path.islink(lock_path):
                try:
                    os.remove(lock_path)
                    logger.warning(f"🗑️ Removed stale {lock_file} for {os.path.basename(profile_dir)}")
                except OSError:
                    pass
        
        # 3. Clean crash artifacts and session restore data
        # Session restore files can cause "Browser window not found" crashes
        for artifact in ['.com.google.Chrome.*']:
            import glob
            for f in glob.glob(os.path.join(profile_dir, artifact)):
                try:
                    os.remove(f)
                except OSError:
                    pass
        
        # Clean crash sentinel and session storage that may crash Chrome on startup
        default_dir = os.path.join(profile_dir, 'Default')
        if os.path.isdir(default_dir):
            for crash_file in ['Current Session', 'Current Tabs', 'Last Session', 'Last Tabs',
                               'Visited Links', 'TransportSecurity']:
                fpath = os.path.join(default_dir, crash_file)
                if os.path.exists(fpath):
                    try:
                        fsize = os.path.getsize(fpath) / (1024 * 1024)
                        if fsize > 10:  # >10MB means likely corrupted
                            os.remove(fpath)
                            logger.warning(f"🗑️ Removed bloated {crash_file} ({fsize:.0f}MB)")
                    except OSError:
                        pass
            # Remove crash sentinel
            sentinel = os.path.join(profile_dir, 'Default', '.org.chromium.Chromium.crash')
            if os.path.exists(sentinel):
                try:
                    os.remove(sentinel)
                except OSError:
                    pass
        
        # 4. Clean caches if profile is too large (>500MB)
        try:
            total_size = sum(
                os.path.getsize(os.path.join(dirpath, filename))
                for dirpath, dirnames, filenames in os.walk(profile_dir)
                for filename in filenames
            ) / (1024 * 1024)  # MB
            
            if total_size > 500:
                logger.warning(f"🧹 Profile too large ({total_size:.0f}MB), cleaning caches")
                import shutil
                for cache_dir in ['Cache', 'Code Cache', 'GPUCache']:
                    cache_path = os.path.join(profile_dir, 'Default', cache_dir)
                    if os.path.isdir(cache_path):
                        shutil.rmtree(cache_path, ignore_errors=True)
                        os.makedirs(cache_path, exist_ok=True)
                for cache_dir in ['GrShaderCache', 'GraphiteDawnCache', 'ShaderCache']:
                    cache_path = os.path.join(profile_dir, cache_dir)
                    if os.path.isdir(cache_path):
                        shutil.rmtree(cache_path, ignore_errors=True)
                        os.makedirs(cache_path, exist_ok=True)
        except Exception as e:
            logger.warning(f"Could not check/clean profile size: {e}")

    def create_browser_session(self, profile_data: Dict, proxy_data: Optional[Dict] = None) -> str:
        """Create a new browser session with specified profile using Playwright."""
        try:
            browser_id = f"browser_{int(time.time())}_{random.randint(1000, 9999)}"

            # Repair profile directory
            profile_dir = os.path.join(settings.browser_user_data_dir, profile_data["name"])
            self.repair_profile_dir(profile_dir)
            singleton_lock = os.path.join(profile_dir, "SingletonLock")
            if os.path.exists(singleton_lock) or os.path.islink(singleton_lock):
                try:
                    os.remove(singleton_lock)
                    logger.warning(f"🗑️ Removed stale SingletonLock for {profile_data['name']}")
                except OSError as e:
                    logger.warning(f"Could not remove SingletonLock: {e}")

            playwright = _get_playwright()

            # Build Playwright launch args
            launch_args = self._build_launch_args(profile_data)
            
            # Build proxy config for Playwright (native auth support!)
            proxy_config = None
            if proxy_data:
                proxy_type = proxy_data.get('proxy_type', 'http')
                proxy_server = f"{proxy_type}://{proxy_data['host']}:{proxy_data['port']}"
                proxy_config = {"server": proxy_server}
                if proxy_data.get('username') and proxy_data.get('password'):
                    proxy_config["username"] = proxy_data['username']
                    proxy_config["password"] = proxy_data['password']
                logger.info(f"✅ Proxy configured: {proxy_server} (auth={'yes' if proxy_config.get('username') else 'no'})")

            # Viewport
            viewport = profile_data.get("viewport", {"width": 1366, "height": 768})
            is_mobile = profile_data.get('is_mobile', False)

            try:
                # Launch persistent context (uses profile directory for cookies/storage)
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=profile_dir,
                    headless=settings.browser_headless,
                    args=launch_args,
                    proxy=proxy_config,
                    viewport={"width": viewport.get("width", 1366), "height": viewport.get("height", 768)},
                    user_agent=profile_data.get("user_agent"),
                    locale="ru-RU",
                    timezone_id=profile_data.get("timezone", "Europe/Moscow"),
                    extra_http_headers={
                        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
                    },
                    is_mobile=is_mobile,
                    has_touch=is_mobile,
                    device_scale_factor=profile_data.get('screen', {}).get('pixel_ratio', 1) if is_mobile else 1,
                    ignore_https_errors=True,
                )
            except Exception as launch_exc:
                logger.warning(f"Chrome launch failed, cleaning up orphans for {profile_dir}: {launch_exc}")
                self._kill_chrome_by_profile_dir(profile_dir)
                singleton_lock = os.path.join(profile_dir, "SingletonLock")
                if os.path.exists(singleton_lock) or os.path.islink(singleton_lock):
                    try:
                        os.remove(singleton_lock)
                    except OSError:
                        pass
                raise launch_exc

            logger.info("✅ Playwright browser created successfully")

            # Apply playwright-stealth BEFORE our custom fingerprint scripts
            # Disable features we handle ourselves with profile-specific values
            try:
                webgl_fp = profile_data.get("webgl_fingerprint", {})
                stealth = Stealth(
                    # NEW features we don't have — ENABLED:
                    chrome_app=True,
                    chrome_csi=True,
                    chrome_load_times=True,
                    hairline=True,
                    media_codecs=True,
                    error_prototype=True,
                    navigator_vendor=True,
                    sec_ch_ua=True,
                    # Features we already handle with profile-specific values — DISABLED:
                    navigator_webdriver=False,      # our CDP script uses profile data
                    navigator_hardware_concurrency=False,  # profile-specific
                    navigator_languages=False,       # profile-specific (ru-RU)
                    navigator_platform=False,        # profile-specific
                    navigator_plugins=False,         # our custom mock
                    navigator_permissions=False,     # our custom patch
                    navigator_user_agent=False,      # set via CDP
                    chrome_runtime=False,            # our custom mock
                    iframe_content_window=False,     # our custom patch
                    webgl_vendor=False,              # profile-specific values
                )
                stealth.apply_stealth_sync(context)
                logger.info("✅ playwright-stealth applied (chrome_app, chrome_csi, chrome_load_times, hairline, media_codecs, error_prototype, navigator_vendor, sec_ch_ua)")
            except Exception as stealth_err:
                logger.warning(f"⚠️ playwright-stealth failed to apply: {stealth_err}")

            # Get or create page
            if context.pages:
                page = context.pages[0]
            else:
                page = context.new_page()

            # Create the Selenium-compatible driver wrapper
            # Note: launch_persistent_context doesn't return a separate Browser object,
            # but context has browser reference if needed
            browser_obj = context.browser
            driver = PlaywrightDriver(
                page=page,
                context=context,
                browser=browser_obj if browser_obj else context,
                playwright_instance=playwright,
            )

            # Apply fingerprint settings via CDP
            self._apply_profile_settings(driver, profile_data)

            # Verify browser is alive
            try:
                _ = driver.current_url
            except Exception as health_err:
                logger.error(f"💀 Browser died during profile setup: {health_err}")
                try:
                    context.close()
                except Exception:
                    pass
                self._kill_chrome_by_profile_dir(profile_dir)
                raise WebDriverException(f"Browser crashed during profile setup: {health_err}")

            # Store browser instance
            self.active_browsers[browser_id] = driver
            self.browser_profiles[browser_id] = profile_data

            # Track PIDs for cleanup
            pids = {'chrome_pid': None, 'profile_dir': profile_dir}
            try:
                pid = driver.browser_pid
                if pid:
                    pids['chrome_pid'] = pid
            except Exception:
                pass
            self.browser_pids[browser_id] = pids

            logger.info(f"Created browser session: {browser_id} (chrome_pid={pids['chrome_pid']})")
            return browser_id

        except Exception as e:
            logger.error(f"Error creating browser session: {e}")
            raise

    def _build_launch_args(self, profile_data: Dict) -> List[str]:
        """Build Chromium launch arguments with per-profile variation.
        
        Core args are always present for stability, but optional args are
        randomly selected per profile to avoid a uniform fingerprint across
        all browser instances.
        """
        # Mandatory args — required for Docker/headless stability
        js_heap_mb = os.environ.get('YANDEX_BOT_BROWSER_JS_HEAP', '4096')
        args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-hang-monitor",
            f"--js-flags=--max-old-space-size={js_heap_mb}",
            "--disable-ipc-flooding-protection",
            # WebRTC: prevent real IP leak through STUN/TURN
            "--enforce-webrtc-ip-permission-check",
            "--webrtc-ip-handling-policy=disable_non_proxied_udp",
        ]

        # Per-profile seed for deterministic but varied flag selection
        import hashlib
        _profile_name = profile_data.get('name', '')
        _seed = int(hashlib.md5(_profile_name.encode()).hexdigest()[:8], 16)
        _rng = random.Random(_seed)

        # Optional args — each has a probability of being included.
        # Real Chrome users have different flag sets depending on version,
        # OS, extensions, and user settings.
        optional_args = [
            ("--disable-background-timer-throttling", 0.7),
            ("--disable-backgrounding-occluded-windows", 0.6),
            ("--disable-renderer-backgrounding", 0.65),
            ("--disable-gpu", 0.5),
            ("--disable-software-rasterizer", 0.4),
            ("--disable-component-extensions-with-background-pages", 0.5),
            ("--disable-features=TranslateUI,BlinkGenPropertyTrees", 0.6),
            ("--disable-features=TranslateUI", 0.3),  # Lighter variant
        ]

        # Track which --disable-features we pick to avoid duplicates
        _has_disable_features = False
        for flag, probability in optional_args:
            if flag.startswith("--disable-features="):
                if _has_disable_features:
                    continue
                if _rng.random() < probability:
                    args.append(flag)
                    _has_disable_features = True
            else:
                if _rng.random() < probability:
                    args.append(flag)

        # Language
        lang = profile_data.get('language', 'ru-RU')
        if ',' in lang:
            lang = lang.split(',')[0].strip()
        args.append(f"--lang={lang}")

        # Stealth flags from profile
        for flag in profile_data.get("chrome_flags", []):
            args.append(flag)

        return args

    def _apply_profile_settings(self, driver: PlaywrightDriver, profile_data: Dict):
        """Apply JavaScript-based profile settings to browser."""
        try:
            is_mobile = profile_data.get('is_mobile', False)
            
            # Viewport is already set via launch_persistent_context,
            # but adjust if mobile needs different window vs viewport
            viewport = profile_data.get("viewport", {})
            if viewport and is_mobile:
                driver.set_window_size(
                    viewport.get("width", 412) + 100,
                    viewport.get("height", 915) + 200
                )

            # Inject fingerprinting scripts
            self._inject_fingerprint_scripts(driver, profile_data)

            # Timezone and locale are set via launch_persistent_context params,
            # but we also set them via CDP for extra robustness
            try:
                driver.execute_cdp_cmd('Emulation.setTimezoneOverride', {
                    'timezoneId': profile_data.get('timezone', 'Europe/Moscow')
                })
            except Exception as e:
                logger.warning(f"Could not set timezone via CDP: {e}")

            try:
                driver.execute_cdp_cmd('Emulation.setLocaleOverride', {
                    'locale': 'ru-RU'
                })
                logger.info("🌐 Locale set to ru-RU via CDP")
            except Exception as e:
                logger.debug(f"Could not set locale via CDP (not critical): {e}")

            try:
                driver.execute_cdp_cmd('Network.setExtraHTTPHeaders', {
                    'headers': {
                        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7'
                    }
                })
                logger.info("🌐 Accept-Language header forced to ru-RU via CDP")
            except Exception as e:
                logger.debug(f"Could not set Accept-Language via CDP: {e}")

            # Mobile device emulation via CDP
            if is_mobile and viewport:
                try:
                    screen = profile_data.get('screen', {})
                    device_scale = screen.get('pixel_ratio', 3)
                    driver.execute_cdp_cmd('Emulation.setDeviceMetricsOverride', {
                        'width': viewport.get('width', 412),
                        'height': viewport.get('height', 915),
                        'deviceScaleFactor': device_scale,
                        'mobile': True,
                        'screenWidth': screen.get('width', viewport.get('width', 412)),
                        'screenHeight': screen.get('height', viewport.get('height', 915)),
                        'screenOrientation': {
                            'type': 'portraitPrimary',
                            'angle': 0
                        }
                    })
                    logger.info(f"📱 Mobile emulation set: {viewport.get('width')}x{viewport.get('height')} @{device_scale}x")
                except Exception as e:
                    logger.warning(f"Could not set mobile device metrics via CDP: {e}")

                try:
                    driver.execute_cdp_cmd('Emulation.setTouchEmulationEnabled', {
                        'enabled': True,
                        'maxTouchPoints': profile_data.get('max_touch_points', 5)
                    })
                    logger.info("📱 Touch emulation enabled")
                except Exception as e:
                    logger.debug(f"Could not enable touch emulation: {e}")

            # Override userAgentData to match the user-agent string
            try:
                import re
                ua_str = profile_data.get('user_agent') or ''
                chrome_match = re.search(r'Chrome/(\d+)\.(\d+)\.(\d+)\.(\d+)', ua_str)
                if chrome_match:
                    major_ver = chrome_match.group(1)
                    full_ver = chrome_match.group(0).replace('Chrome/', '')
                    
                    if is_mobile:
                        ua_platform = 'Android'
                        mobile_device = profile_data.get('mobile_device', {})
                        platform_ver = mobile_device.get('android', '14') + '.0.0'
                        model = mobile_device.get('model', '')
                        architecture = ''
                        bitness = ''
                    else:
                        model = ''
                        if 'Windows' in ua_str:
                            ua_platform = 'Windows'
                            platform_ver = '15.0.0' if 'Windows NT 10' in ua_str else '10.0.0'
                        elif 'Macintosh' in ua_str or 'Mac OS X' in ua_str:
                            ua_platform = 'macOS'
                            platform_ver = '14.7.6'
                        else:
                            ua_platform = 'Linux'
                            platform_ver = '6.5.0'
                        architecture = 'x86' if 'x86' in ua_str or 'Win' in ua_str else 'arm'
                        bitness = '64'
                    
                    driver.execute_cdp_cmd('Emulation.setUserAgentOverride', {
                        'userAgent': ua_str,
                        'platform': profile_data.get('platform', 'Linux armv81' if is_mobile else 'Win32'),
                        'userAgentMetadata': {
                            'brands': [
                                {'brand': 'Chromium', 'version': major_ver},
                                {'brand': 'Google Chrome', 'version': major_ver},
                                {'brand': 'Not-A.Brand', 'version': '99'}
                            ],
                            'fullVersionList': [
                                {'brand': 'Chromium', 'version': full_ver},
                                {'brand': 'Google Chrome', 'version': full_ver},
                                {'brand': 'Not-A.Brand', 'version': '99.0.0.0'}
                            ],
                            'fullVersion': full_ver,
                            'platform': ua_platform,
                            'platformVersion': platform_ver,
                            'architecture': architecture if is_mobile else ('x86' if 'x86' in ua_str or 'Win' in ua_str else 'arm'),
                            'model': model,
                            'mobile': is_mobile,
                            'bitness': bitness if is_mobile else '64',
                            'wow64': False
                        }
                    })
                    device_label = f"📱 {model}" if is_mobile else f"🖥️ {ua_platform}"
                    logger.info(f"🛡️ userAgentData override set: Chrome/{major_ver} {device_label}")
            except Exception as e:
                logger.warning(f"Could not override userAgentData: {e}")

            logger.info(f"Applied profile settings for: {profile_data['name']} ({'mobile' if is_mobile else 'desktop'})")

        except Exception as e:
            error_str = str(e)
            fatal_keywords = ['Browser window not found', 'invalid session id', 'session deleted',
                              'browser has closed', 'not reachable', 'disconnected', 'Target closed']
            if any(kw.lower() in error_str.lower() for kw in fatal_keywords):
                logger.error(f"💀 Fatal error applying profile settings (browser crashed): {e}")
                raise
            logger.error(f"Error applying profile settings: {e}")

    def _inject_fingerprint_scripts(self, driver: PlaywrightDriver, profile_data: Dict):
        """Inject JavaScript to modify browser fingerprints via CDP.
        
        Uses Page.addScriptToEvaluateOnNewDocument so scripts execute
        BEFORE any page JavaScript — making overrides undetectable.
        """
        try:
            webgl_data = profile_data.get("webgl_fingerprint", {})
            hw_concurrency = profile_data.get('hardware_concurrency', 4)
            dev_memory = profile_data.get('device_memory', 8)
            platform = profile_data.get("platform", "Win32")
            max_touch = profile_data.get('max_touch_points', 0)
            webgl_vendor = webgl_data.get("unmaskedVendor", "Google Inc. (NVIDIA)")
            webgl_renderer = webgl_data.get("unmaskedRenderer", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1060 6GB Direct3D11 vs_5_0 ps_5_0, D3D11)")
            
            # Screen dimensions from profile
            screen_data = profile_data.get("screen", {})
            screen_width = screen_data.get("width", 1920)
            screen_height = screen_data.get("height", 1080)
            color_depth = screen_data.get("color_depth", 24)
            pixel_ratio = screen_data.get("pixel_ratio", 1)
            
            # Viewport from profile
            viewport_data = profile_data.get("viewport", {})
            viewport_width = viewport_data.get("width", 1366)
            viewport_height = viewport_data.get("height", 768)
            
            is_mobile = profile_data.get("is_mobile", False)

            # WebGPU profile data
            webgpu_data = profile_data.get("webgpu_fingerprint", {})

            # Sensor data (mobile only)
            sensor_data = profile_data.get("sensor", {})

            # CSS media queries
            css_media = profile_data.get("css_media", {})

            # Speech synthesis voices
            speech_voices = profile_data.get("speech_voices", [])

            # Feature detection flags
            feature_flags = profile_data.get("feature_flags", {})

            # Audio properties
            audio_props = profile_data.get("audio_properties", {})

            # New fingerprint vectors
            connection_info = profile_data.get("connection_info", {})
            storage_quota = profile_data.get("storage_quota", 599720927232)
            heap_size = profile_data.get("heap_size", 4294705152)
            system_colors = profile_data.get("system_colors", {})
            system_fonts_list = profile_data.get("system_fonts", [])
            codecs_list = profile_data.get("codecs", [])
            keyboard_layout = profile_data.get("keyboard_layout", [])
            fonts_list = profile_data.get("fonts", [])

            # Extract chrome version from user_agent for userAgentData mock
            import re as _re
            _ua = profile_data.get("user_agent", "")
            _cv_match = _re.search(r'Chrome/(\d+[\.\d]*)', _ua)
            chrome_version = _cv_match.group(1) if _cv_match else "131.0.0.0"
            # Platform name for userAgentData (Windows/macOS/Linux)
            _plat = profile_data.get("platform", "Win32")
            if "Win" in _plat:
                platform_name = "Windows"
            elif "Mac" in _plat or "iPhone" in _plat or "iPad" in _plat:
                platform_name = "macOS"
            elif "Linux" in _plat or "Android" in _plat:
                platform_name = "Linux"
            else:
                platform_name = "Windows"

            # Serialize WebGL profile data as JSON for JS injection
            import json as _json
            webgl_profile_json = _json.dumps(webgl_data)

            # Canvas noise seed: deterministic per profile so the fingerprint is
            # unique but consistent across page loads (defeats averaging attacks).
            # Uses canvas_fingerprint string or profile name as seed source.
            _canvas_fp = profile_data.get('canvas_fingerprint', profile_data.get('name', 'default'))
            import hashlib as _hl
            _canvas_hash = _hl.md5(str(_canvas_fp).encode()).hexdigest()
            canvas_seed = int(_canvas_hash[:8], 16)  # 32-bit integer seed

            # Single comprehensive stealth script injected via CDP
            stealth_script = f"""
            // ===== Stealth fingerprint overrides =====
            // Runs BEFORE page JS via Page.addScriptToEvaluateOnNewDocument

            // --- Remove webdriver flag ---
            try {{
                Object.defineProperty(navigator, 'webdriver', {{
                    get: () => undefined,
                    configurable: true
                }});
                // Also delete from prototype
                delete navigator.__proto__.webdriver;
            }} catch(e) {{}}

            // --- Navigator overrides using proper prototype patching ---
            const navigatorOverrides = {{
                hardwareConcurrency: {hw_concurrency},
                deviceMemory: {dev_memory},
                platform: '{platform}',
                language: 'ru-RU',
                languages: Object.freeze(['ru-RU', 'ru', 'en-US', 'en']),
                maxTouchPoints: {max_touch}
            }};

            for (const [prop, value] of Object.entries(navigatorOverrides)) {{
                try {{
                    const descriptor = {{
                        get: () => value,
                        configurable: true,
                        enumerable: true
                    }};
                    Object.defineProperty(Navigator.prototype, prop, descriptor);
                }} catch(e) {{}}
            }}

            // --- Screen dimensions override (match profile, not server) ---
            try {{
                const screenOverrides = {{
                    width: {screen_width},
                    height: {screen_height},
                    availWidth: {screen_width},
                    availHeight: {screen_height} - 40,
                    colorDepth: {color_depth},
                    pixelDepth: {color_depth}
                }};
                for (const [prop, value] of Object.entries(screenOverrides)) {{
                    Object.defineProperty(Screen.prototype, prop, {{
                        get: () => value,
                        configurable: true,
                        enumerable: true
                    }});
                }}
                Object.defineProperty(window, 'devicePixelRatio', {{
                    get: () => {pixel_ratio},
                    configurable: true
                }});
                Object.defineProperty(window, 'innerWidth', {{
                    get: () => {viewport_width},
                    configurable: true
                }});
                Object.defineProperty(window, 'innerHeight', {{
                    get: () => {viewport_height},
                    configurable: true
                }});
                Object.defineProperty(window, 'outerWidth', {{
                    get: () => {screen_width},
                    configurable: true
                }});
                Object.defineProperty(window, 'outerHeight', {{
                    get: () => {screen_height},
                    configurable: true
                }});
            }} catch(e) {{}}

            // --- navigator.connection mock ---
            try {{
                const connectionData = {{
                    effectiveType: '{"3g" if is_mobile else "4g"}',
                    rtt: {'100' if is_mobile else '50'},
                    downlink: {'2.5' if is_mobile else '10'},
                    saveData: false,
                    type: '{"cellular" if is_mobile else "wifi"}',
                    onchange: null
                }};
                const connectionProxy = new Proxy(connectionData, {{
                    get(target, prop) {{
                        if (prop === Symbol.toStringTag) return 'NetworkInformation';
                        return target[prop];
                    }}
                }});
                Object.defineProperty(Navigator.prototype, 'connection', {{
                    get: () => connectionProxy,
                    configurable: true,
                    enumerable: true
                }});
            }} catch(e) {{}}

            // --- Battery API mock ---
            try {{
                const batteryData = {{
                    charging: {'true' if not is_mobile else 'Math.random() > 0.3'},
                    chargingTime: {'0' if not is_mobile else 'Math.floor(Math.random() * 3600)'},
                    dischargingTime: Infinity,
                    level: {'1.0' if not is_mobile else '(0.3 + Math.random() * 0.65).toFixed(2)'},
                    addEventListener: function() {{}},
                    removeEventListener: function() {{}},
                    dispatchEvent: function() {{ return true; }}
                }};
                if (navigator.getBattery) {{
                    Navigator.prototype.getBattery = function() {{
                        return Promise.resolve(batteryData);
                    }};
                }}
            }} catch(e) {{}}

            // --- WebRTC IP leak protection ---
            try {{
                const origRTCPeerConnection = window.RTCPeerConnection || window.webkitRTCPeerConnection;
                if (origRTCPeerConnection) {{
                    const wrappedRTC = function(config, constraints) {{
                        // Force all ICE through proxy by restricting to relay-only
                        if (config && config.iceServers) {{
                            config.iceTransportPolicy = 'relay';
                        }}
                        if (!config) config = {{}};
                        config.iceTransportPolicy = 'relay';
                        return new origRTCPeerConnection(config, constraints);
                    }};
                    wrappedRTC.prototype = origRTCPeerConnection.prototype;
                    wrappedRTC.generateCertificate = origRTCPeerConnection.generateCertificate;
                    window.RTCPeerConnection = wrappedRTC;
                    if (window.webkitRTCPeerConnection) {{
                        window.webkitRTCPeerConnection = wrappedRTC;
                    }}
                }}
            }} catch(e) {{}}

            // --- Performance.now() noise (anti-timing fingerprint) ---
            try {{
                const origPerfNow = Performance.prototype.now;
                const perfOffset = Math.random() * 0.1;
                Performance.prototype.now = function() {{
                    const real = origPerfNow.call(this);
                    // Add tiny random noise (0-100μs) to prevent timing fingerprint
                    return real + perfOffset + (Math.random() * 0.1);
                }};
            }} catch(e) {{}}

            // --- Canvas fingerprint noise (seed-based, per-profile stable) ---
            try {{
                const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
                const origToBlob = HTMLCanvasElement.prototype.toBlob;
                const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;

                // Seeded PRNG (mulberry32) — deterministic per profile,
                // produces the same noise on every call so fingerprint is
                // unique to this profile but stable across measurements.
                const CANVAS_SEED = {canvas_seed};
                function mulberry32(seed) {{
                    return function() {{
                        seed |= 0; seed = seed + 0x6D2B79F5 | 0;
                        var t = Math.imul(seed ^ seed >>> 15, 1 | seed);
                        t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
                        return ((t ^ t >>> 14) >>> 0) / 4294967296;
                    }};
                }}
                const canvasRng = mulberry32(CANVAS_SEED);

                // Pre-generate per-profile noise offsets (stable across calls)
                const NOISE_TABLE_SIZE = 256;
                const noiseTable = new Int8Array(NOISE_TABLE_SIZE);
                for (let i = 0; i < NOISE_TABLE_SIZE; i++) {{
                    // Values in range [-2, +2] — imperceptible but unique
                    noiseTable[i] = Math.floor(canvasRng() * 5) - 2;
                }}
                // Per-profile pixel modification rate (0.5% - 3%)
                const noiseRate = 0.005 + canvasRng() * 0.025;
                // Per-profile sub-pixel color channel offset
                const channelOffset = Math.floor(canvasRng() * 3);  // 0=R, 1=G, 2=B

                function addProfileNoise(imageData) {{
                    const data = imageData.data;
                    const len = data.length;
                    // Use seeded RNG instance for this call (deterministic)
                    const callRng = mulberry32(CANVAS_SEED ^ (len & 0xFFFF));
                    for (let i = 0; i < len; i += 4) {{
                        if (callRng() < noiseRate) {{
                            const idx = (i >> 2) & (NOISE_TABLE_SIZE - 1);
                            const ch = channelOffset + (idx & 1);  // Alternate channels
                            data[i + ch] = Math.max(0, Math.min(255, data[i + ch] + noiseTable[idx]));
                        }}
                    }}
                    return imageData;
                }}

                CanvasRenderingContext2D.prototype.getImageData = function(...args) {{
                    const imageData = origGetImageData.apply(this, args);
                    return addProfileNoise(imageData);
                }};

                // Deterministic sub-pixel rgba noise before toDataURL/toBlob
                const _profileNoiseR = (CANVAS_SEED & 0xFF) / 25500;       // 0.00..0.01
                const _profileNoiseG = ((CANVAS_SEED >> 8) & 0xFF) / 25500;
                const _profileNoiseB = ((CANVAS_SEED >> 16) & 0xFF) / 25500;

                HTMLCanvasElement.prototype.toDataURL = function(...args) {{
                    try {{
                        const ctx = this.getContext('2d');
                        if (ctx) {{
                            ctx.fillStyle = `rgba(${{(CANVAS_SEED & 0xF)}},${{((CANVAS_SEED >> 4) & 0xF)}},${{((CANVAS_SEED >> 8) & 0xF)}},${{_profileNoiseR}})`;
                            ctx.fillRect(0, 0, 1, 1);
                        }}
                    }} catch(e) {{}}
                    return origToDataURL.apply(this, args);
                }};

                HTMLCanvasElement.prototype.toBlob = function(cb, ...args) {{
                    try {{
                        const ctx = this.getContext('2d');
                        if (ctx) {{
                            ctx.fillStyle = `rgba(${{(CANVAS_SEED & 0xF)}},${{((CANVAS_SEED >> 4) & 0xF)}},${{((CANVAS_SEED >> 8) & 0xF)}},${{_profileNoiseG}})`;
                            ctx.fillRect(0, 0, 1, 1);
                        }}
                    }} catch(e) {{}}
                    return origToBlob.call(this, cb, ...args);
                }};
            }} catch(e) {{}}

            // --- WebGL vendor/renderer + comprehensive parameters override ---
            try {{
                const _wgp = {webgl_profile_json};

                // --- WebGL1 parameter overrides ---
                const webgl1Overrides = {{
                    37445: _wgp.unmaskedVendor,   // UNMASKED_VENDOR_WEBGL
                    37446: _wgp.unmaskedRenderer,  // UNMASKED_RENDERER_WEBGL
                    7936: _wgp.vendor || 'WebKit',            // VENDOR
                    7937: _wgp.renderer || 'WebKit WebGL',    // RENDERER
                    7938: _wgp.version,            // VERSION
                    35724: _wgp.shadingLanguage,   // SHADING_LANGUAGE_VERSION
                    3379: parseInt(_wgp.maxTextureSize),       // MAX_TEXTURE_SIZE
                    3386: new Int32Array(_wgp.maxViewportDims),  // MAX_VIEWPORT_DIMS
                    34921: parseInt(_wgp.maxVertexAttribs),    // MAX_VERTEX_ATTRIBS
                    36349: parseInt(_wgp.maxFragmentUniformVectors),  // MAX_FRAGMENT_UNIFORM_VECTORS
                    36347: parseInt(_wgp.maxVertexUniformVectors),    // MAX_VERTEX_UNIFORM_VECTORS
                    34076: parseInt(_wgp.maxCubeMapTextureSize),      // MAX_CUBE_MAP_TEXTURE_SIZE
                    34024: parseInt(_wgp.maxRenderBufferSize),        // MAX_RENDERBUFFER_SIZE
                    35661: parseInt(_wgp.maxCombinedTextureImageUnits),  // MAX_COMBINED_TEXTURE_IMAGE_UNITS
                    34930: parseInt(_wgp.maxTextureImageUnits),       // MAX_TEXTURE_IMAGE_UNITS
                    35660: parseInt(_wgp.maxVertexTextureImageUnits), // MAX_VERTEX_TEXTURE_IMAGE_UNITS
                    36348: parseInt(_wgp.maxVaryingVectors),          // MAX_VARYING_VECTORS
                    3413: parseInt(_wgp.sampleBuffers),               // SAMPLE_BUFFERS
                    3415: parseInt(_wgp.samples),                     // SAMPLES
                    3408: new Float32Array(_wgp.aliasedLineWidthRange),  // ALIASED_LINE_WIDTH_RANGE
                    3414: new Float32Array(_wgp.aliasedPointSizeRange),  // ALIASED_POINT_SIZE_RANGE
                    3410: parseInt(_wgp.alphaBits),      // ALPHA_BITS
                    3412: parseInt(_wgp.blueBits),       // BLUE_BITS
                    3411: parseInt(_wgp.greenBits),      // GREEN_BITS
                    3410: parseInt(_wgp.redBits),        // RED_BITS  → note: 3409 is RED_BITS, 3410 is GREEN
                    3414: new Float32Array(_wgp.aliasedPointSizeRange),
                    3416: parseInt(_wgp.depthBits),      // DEPTH_BITS
                    3415: parseInt(_wgp.samples),
                    36003: parseInt(_wgp.stencilBits),   // STENCIL_BITS
                    36004: parseInt(_wgp.subpixelBits),  // SUBPIXEL_BITS
                    36005: parseInt(_wgp.stencilBackValueMask),   // STENCIL_BACK_VALUE_MASK
                    36006: parseInt(_wgp.stencilBackWritemask),   // STENCIL_BACK_WRITEMASK
                    2967: parseInt(_wgp.stencilValueMask),        // STENCIL_VALUE_MASK
                    2968: parseInt(_wgp.stencilWritemask),        // STENCIL_WRITEMASK
                    34047: parseInt(_wgp.maxAnisotropy || '16'),  // MAX_TEXTURE_MAX_ANISOTROPY_EXT
                }};

                // --- WebGL2 additional parameter overrides ---
                const webgl2Overrides = Object.assign({{}}, webgl1Overrides, {{
                    7938: _wgp.version2,              // VERSION (WebGL 2.0)
                    35724: _wgp.shadingLanguage2,     // SHADING_LANGUAGE_VERSION
                    // WebGL2-specific limits
                    35371: parseInt(_wgp.maxVertexUniformComponents2),    // MAX_VERTEX_UNIFORM_COMPONENTS
                    35374: parseInt(_wgp.maxVertexUniformBlocks2),        // MAX_VERTEX_UNIFORM_BLOCKS
                    36122: parseInt(_wgp.maxVertexOutputComponents2),     // MAX_VERTEX_OUTPUT_COMPONENTS
                    35659: parseInt(_wgp.maxVaryingComponents2),          // MAX_VARYING_COMPONENTS
                    35977: parseInt(_wgp.maxTransformFeedbackInterleavedComponents2),  // MAX_TRANSFORM_FEEDBACK_INTERLEAVED_COMPONENTS
                    35979: parseInt(_wgp.maxTransformFeedbackSeparateAttribs2),        // MAX_TRANSFORM_FEEDBACK_SEPARATE_ATTRIBS
                    35981: parseInt(_wgp.maxTransformFeedbackSeparateComponents2),     // MAX_TRANSFORM_FEEDBACK_SEPARATE_COMPONENTS
                    35657: parseInt(_wgp.maxFragmentUniformComponents2),  // MAX_FRAGMENT_UNIFORM_COMPONENTS
                    35373: parseInt(_wgp.maxFragmentUniformBlocks2),      // MAX_FRAGMENT_UNIFORM_BLOCKS
                    37157: parseInt(_wgp.maxFragmentInputComponents2),    // MAX_FRAGMENT_INPUT_COMPONENTS
                    36346: parseInt(_wgp.minProgramTexelOffset2),         // MIN_PROGRAM_TEXEL_OFFSET
                    36345: parseInt(_wgp.maxProgramTexelOffset2),         // MAX_PROGRAM_TEXEL_OFFSET
                    34852: parseInt(_wgp.maxDrawBuffers2),     // MAX_DRAW_BUFFERS
                    36063: parseInt(_wgp.maxColorAttachments2),  // MAX_COLOR_ATTACHMENTS
                    36183: parseInt(_wgp.maxSamples2),          // MAX_SAMPLES
                    32883: parseInt(_wgp.max3DTextureSize2),    // MAX_3D_TEXTURE_SIZE
                    35071: parseInt(_wgp.maxArrayTextureLayers2),  // MAX_ARRAY_TEXTURE_LAYERS
                    36203: parseInt(_wgp.maxClientWaitTimeoutWebgl2 || '0'),
                    36202: _wgp.maxElementIndex2,               // MAX_ELEMENT_INDEX
                    36205: parseInt(_wgp.maxServerWaitTimeout2 || '0'),
                    34045: parseFloat(_wgp.maxTextureLodBias2 || '2'),  // MAX_TEXTURE_LOD_BIAS
                    35375: parseInt(_wgp.maxUniformBufferBindings2),    // MAX_UNIFORM_BUFFER_BINDINGS
                    35376: parseInt(_wgp.maxUniformBlockSize2),         // MAX_UNIFORM_BLOCK_SIZE
                    35380: parseInt(_wgp.uniformBufferOffsetAlignment2),  // UNIFORM_BUFFER_OFFSET_ALIGNMENT
                    35377: parseInt(_wgp.maxCombinedUniformBlocks2),    // MAX_COMBINED_UNIFORM_BLOCKS
                    35378: parseInt(_wgp.maxCombinedVertexUniformComponents2),
                    35379: parseInt(_wgp.maxCombinedFragmentUniformComponents2),
                    33000: parseInt(_wgp.maxElementsVertices2 || '2147483647'),
                    33001: parseInt(_wgp.maxElementsIndices2 || '2147483647'),
                    // WGL2 also overrides the same basic limits
                    3379: parseInt(_wgp.maxTextureSize2 || _wgp.maxTextureSize),
                    3386: new Int32Array(_wgp.maxViewportDims2 || _wgp.maxViewportDims),
                    34076: parseInt(_wgp.maxCubeMapTextureSize2 || _wgp.maxCubeMapTextureSize),
                    34024: parseInt(_wgp.maxRenderBufferSize2 || _wgp.maxRenderBufferSize),
                    35661: parseInt(_wgp.maxCombinedTextureImageUnits2 || _wgp.maxCombinedTextureImageUnits),
                    34930: parseInt(_wgp.maxTextureImageUnits2 || _wgp.maxTextureImageUnits),
                    35660: parseInt(_wgp.maxVertexTextureImageUnits2 || _wgp.maxVertexTextureImageUnits),
                    36348: parseInt(_wgp.maxVaryingVectors2 || _wgp.maxVaryingVectors),
                    34921: parseInt(_wgp.maxVertexAttribs2 || _wgp.maxVertexAttribs),
                    36349: parseInt(_wgp.maxFragmentUniformVectors2 || _wgp.maxFragmentUniformVectors),
                    36347: parseInt(_wgp.maxVertexUniformVectors2 || _wgp.maxVertexUniformVectors),
                }});

                // --- Patch getParameter for WebGL1 ---
                const origGetParam1 = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(param) {{
                    if (webgl1Overrides.hasOwnProperty(param)) {{
                        return webgl1Overrides[param];
                    }}
                    return origGetParam1.call(this, param);
                }};

                // --- Patch getParameter for WebGL2 ---
                if (typeof WebGL2RenderingContext !== 'undefined') {{
                    const origGetParam2 = WebGL2RenderingContext.prototype.getParameter;
                    WebGL2RenderingContext.prototype.getParameter = function(param) {{
                        if (webgl2Overrides.hasOwnProperty(param)) {{
                            return webgl2Overrides[param];
                        }}
                        return origGetParam2.call(this, param);
                    }};
                }}

                // --- Patch getSupportedExtensions ---
                const _wgl1Exts = (_wgp.extensions || '').split(',').map(s => s.trim()).filter(Boolean);
                const _wgl2Exts = (_wgp.extensions2 || '').split(',').map(s => s.trim()).filter(Boolean);
                const origGetExts1 = WebGLRenderingContext.prototype.getSupportedExtensions;
                WebGLRenderingContext.prototype.getSupportedExtensions = function() {{
                    return _wgl1Exts.length > 0 ? _wgl1Exts : origGetExts1.call(this);
                }};
                if (typeof WebGL2RenderingContext !== 'undefined') {{
                    const origGetExts2 = WebGL2RenderingContext.prototype.getSupportedExtensions;
                    WebGL2RenderingContext.prototype.getSupportedExtensions = function() {{
                        return _wgl2Exts.length > 0 ? _wgl2Exts : origGetExts2.call(this);
                    }};
                }}

                // --- Patch getContextAttributes ---
                const _ctxAttrs1 = _wgp.contextAttributes || null;
                const _ctxAttrs2 = _wgp.contextAttributes2 || _ctxAttrs1;
                if (_ctxAttrs1) {{
                    const origGetCtxAttr1 = WebGLRenderingContext.prototype.getContextAttributes;
                    WebGLRenderingContext.prototype.getContextAttributes = function() {{
                        const real = origGetCtxAttr1.call(this);
                        return Object.assign({{}}, real, _ctxAttrs1);
                    }};
                }}
                if (_ctxAttrs2 && typeof WebGL2RenderingContext !== 'undefined') {{
                    const origGetCtxAttr2 = WebGL2RenderingContext.prototype.getContextAttributes;
                    WebGL2RenderingContext.prototype.getContextAttributes = function() {{
                        const real = origGetCtxAttr2.call(this);
                        return Object.assign({{}}, real, _ctxAttrs2);
                    }};
                }}

                // --- Patch getShaderPrecisionFormat ---
                const _precData = _wgp.precision || null;
                if (_precData) {{
                    function patchShaderPrecision(proto) {{
                        const orig = proto.getShaderPrecisionFormat;
                        proto.getShaderPrecisionFormat = function(shaderType, precisionType) {{
                            const real = orig.call(this, shaderType, precisionType);
                            // Map shaderType + precisionType to key
                            const shaderNames = {{35633: 'vertexShader', 35632: 'fragmentShader'}};
                            const precNames = {{36336: 'HighFloat', 36337: 'MediumFloat', 36338: 'LowFloat',
                                               36339: 'HighInt', 36340: 'MediumInt', 36341: 'LowInt'}};
                            const sName = shaderNames[shaderType];
                            const pName = precNames[precisionType];
                            if (sName && pName) {{
                                const key = sName + pName;
                                const val = _precData[key];
                                if (val) {{
                                    return {{
                                        precision: val.precision,
                                        rangeMin: val.rangeMin,
                                        rangeMax: val.rangeMax
                                    }};
                                }}
                            }}
                            return real;
                        }};
                    }}
                    patchShaderPrecision(WebGLRenderingContext.prototype);
                    if (typeof WebGL2RenderingContext !== 'undefined') {{
                        patchShaderPrecision(WebGL2RenderingContext.prototype);
                    }}
                }}
            }} catch(e) {{}}

            // --- WebGPU adapter/device mock ---
            try {{
                const _wgpuData = {_json.dumps(webgpu_data)};
                if (_wgpuData.isEnabled && navigator.gpu) {{
                    // Build a fake GPUSupportedLimits object from dict
                    function makeLimits(limDict) {{
                        const obj = {{}};
                        for (const [k, v] of Object.entries(limDict)) {{
                            obj[k] = typeof v === 'string' ? parseInt(v) || 0 : v;
                        }}
                        return obj;
                    }}
                    // Build a fake GPUSupportedFeatures (Set-like)
                    function makeFeatures(arr) {{
                        const s = new Set(arr);
                        return s;
                    }}
                    function makeAdapterInfo(info) {{
                        return {{
                            vendor: info.vendor || '',
                            architecture: info.architecture || '',
                            device: info.device || '',
                            description: info.description || '',
                        }};
                    }}
                    function buildAdapter(adapterData, deviceLimits) {{
                        const adapter = {{
                            isFallbackAdapter: adapterData.isFallbackAdapter || false,
                            features: makeFeatures(adapterData.features || []),
                            limits: makeLimits(adapterData.limits || {{}}),
                            info: makeAdapterInfo(adapterData.info || {{}}),
                            requestDevice: function(descriptor) {{
                                const dev = {{
                                    features: makeFeatures(adapterData.features || []),
                                    limits: makeLimits(deviceLimits || adapterData.limits_gpudevice || {{}}),
                                    queue: {{
                                        label: '',
                                        submit: function() {{}},
                                        onSubmittedWorkDone: function() {{ return Promise.resolve(); }},
                                        writeBuffer: function() {{}},
                                        writeTexture: function() {{}},
                                        copyExternalImageToTexture: function() {{}},
                                    }},
                                    label: '',
                                    lost: new Promise(function() {{}}),
                                    destroy: function() {{}},
                                    createBuffer: function() {{ return {{}}; }},
                                    createTexture: function() {{ return {{}}; }},
                                    createSampler: function() {{ return {{}}; }},
                                    createBindGroupLayout: function() {{ return {{}}; }},
                                    createPipelineLayout: function() {{ return {{}}; }},
                                    createShaderModule: function() {{ return {{}}; }},
                                    createComputePipeline: function() {{ return {{}}; }},
                                    createRenderPipeline: function() {{ return {{}}; }},
                                    createCommandEncoder: function() {{ return {{}}; }},
                                    createRenderBundleEncoder: function() {{ return {{}}; }},
                                    createQuerySet: function() {{ return {{}}; }},
                                    pushErrorScope: function() {{}},
                                    popErrorScope: function() {{ return Promise.resolve(null); }},
                                }};
                                return Promise.resolve(dev);
                            }},
                            requestAdapterInfo: function() {{
                                return Promise.resolve(makeAdapterInfo(adapterData.info || {{}}));
                            }},
                        }};
                        return adapter;
                    }}

                    const _hp = _wgpuData.highPerformance;
                    const _lp = _wgpuData.lowPerformance;

                    const origRequestAdapter = navigator.gpu.requestAdapter.bind(navigator.gpu);
                    navigator.gpu.requestAdapter = function(options) {{
                        const powerPref = (options && options.powerPreference) || 'high-performance';
                        const src = (powerPref === 'low-power' && _lp) ? _lp : _hp;
                        if (src) {{
                            return Promise.resolve(buildAdapter(src, src.limits_gpudevice || {{}}));
                        }}
                        return origRequestAdapter(options);
                    }};

                    // Override getPreferredCanvasFormat
                    if (_wgpuData.preferredCanvasFormat) {{
                        navigator.gpu.getPreferredCanvasFormat = function() {{
                            return _wgpuData.preferredCanvasFormat;
                        }};
                    }}
                }}
            }} catch(e) {{}}

            // --- Device Sensor APIs (mobile) ---
            try {{
                const _sensorData = {_json.dumps(sensor_data)};
                if (_sensorData && _sensorData.gyroscope) {{
                    // Gyroscope mock
                    if (typeof Gyroscope !== 'undefined') {{
                        const OrigGyro = Gyroscope;
                        window.Gyroscope = function(opts) {{
                            const inst = new OrigGyro(opts);
                            Object.defineProperties(inst, {{
                                x: {{ get: () => _sensorData.gyroscope.x }},
                                y: {{ get: () => _sensorData.gyroscope.y }},
                                z: {{ get: () => _sensorData.gyroscope.z }},
                            }});
                            return inst;
                        }};
                        window.Gyroscope.prototype = OrigGyro.prototype;
                    }}
                    // Accelerometer mock
                    if (typeof Accelerometer !== 'undefined') {{
                        const OrigAccel = Accelerometer;
                        window.Accelerometer = function(opts) {{
                            const inst = new OrigAccel(opts);
                            Object.defineProperties(inst, {{
                                x: {{ get: () => _sensorData.accelerometer.x }},
                                y: {{ get: () => _sensorData.accelerometer.y }},
                                z: {{ get: () => _sensorData.accelerometer.z }},
                            }});
                            return inst;
                        }};
                        window.Accelerometer.prototype = OrigAccel.prototype;
                    }}
                    // GravitySensor mock
                    if (typeof GravitySensor !== 'undefined') {{
                        const OrigGrav = GravitySensor;
                        window.GravitySensor = function(opts) {{
                            const inst = new OrigGrav(opts);
                            Object.defineProperties(inst, {{
                                x: {{ get: () => _sensorData.gravity.x }},
                                y: {{ get: () => _sensorData.gravity.y }},
                                z: {{ get: () => _sensorData.gravity.z }},
                            }});
                            return inst;
                        }};
                        window.GravitySensor.prototype = OrigGrav.prototype;
                    }}
                    // LinearAccelerationSensor mock
                    if (typeof LinearAccelerationSensor !== 'undefined') {{
                        const OrigLin = LinearAccelerationSensor;
                        window.LinearAccelerationSensor = function(opts) {{
                            const inst = new OrigLin(opts);
                            Object.defineProperties(inst, {{
                                x: {{ get: () => _sensorData.linearAcceleration.x }},
                                y: {{ get: () => _sensorData.linearAcceleration.y }},
                                z: {{ get: () => _sensorData.linearAcceleration.z }},
                            }});
                            return inst;
                        }};
                        window.LinearAccelerationSensor.prototype = OrigLin.prototype;
                    }}
                }}
            }} catch(e) {{}}

            // --- CSS matchMedia overrides ---
            try {{
                const _cssMedia = {_json.dumps(css_media)};
                if (_cssMedia && Object.keys(_cssMedia).length > 0) {{
                    const origMatchMedia = window.matchMedia;
                    const mediaMap = {{
                        '(hover: hover)': _cssMedia.hover === 'hover',
                        '(hover: none)': _cssMedia.hover === 'none',
                        '(pointer: fine)': _cssMedia.pointer === 'fine',
                        '(pointer: coarse)': _cssMedia.pointer === 'coarse',
                        '(any-hover: hover)': _cssMedia.anyHover === 'hover',
                        '(any-hover: none)': _cssMedia.anyHover === 'none',
                        '(any-pointer: fine)': _cssMedia.anyPointer === 'fine',
                        '(any-pointer: coarse)': _cssMedia.anyPointer === 'coarse',
                        '(prefers-color-scheme: dark)': _cssMedia.prefersColorScheme === 'dark',
                        '(prefers-color-scheme: light)': _cssMedia.prefersColorScheme === 'light',
                        '(prefers-reduced-motion: reduce)': _cssMedia.prefersReducedMotion === 'reduce',
                        '(prefers-reduced-motion: no-preference)': _cssMedia.prefersReducedMotion === 'no-preference',
                        '(prefers-contrast: more)': _cssMedia.prefersContrast === 'more',
                        '(prefers-contrast: no-preference)': _cssMedia.prefersContrast === 'no-preference',
                        '(forced-colors: active)': _cssMedia.forcedColors === 'active',
                        '(forced-colors: none)': _cssMedia.forcedColors === 'none',
                        '(inverted-colors: inverted)': _cssMedia.invertedColors === 'inverted',
                        '(inverted-colors: none)': _cssMedia.invertedColors === 'none',
                        '(dynamic-range: high)': _cssMedia.dynamicRange === 'high',
                        '(dynamic-range: standard)': _cssMedia.dynamicRange === 'standard',
                        // New CSS media queries
                        '(color-gamut: srgb)': _cssMedia.colorGamut === 'srgb',
                        '(color-gamut: p3)': _cssMedia.colorGamut === 'p3',
                        '(color-gamut: rec2020)': _cssMedia.colorGamut === 'rec2020',
                        '(color: 8)': _cssMedia.color === 8,
                        '(color: 0)': _cssMedia.color === 0,
                        '(color-index: 0)': _cssMedia.colorIndex === 0,
                        '(grid: 0)': _cssMedia.grid === 0,
                        '(monochrome: 0)': _cssMedia.monochrome === 0,
                        '(orientation: landscape)': _cssMedia.orientation === 'landscape',
                        '(orientation: portrait)': _cssMedia.orientation === 'portrait',
                        '(overflow-block: scroll)': _cssMedia.overflowBlock === 'scroll',
                        '(overflow-block: none)': _cssMedia.overflowBlock === 'none',
                        '(prefers-reduced-transparency: no-preference)': _cssMedia.prefersReducedTransparency === 'no-preference',
                        '(prefers-reduced-transparency: reduce)': _cssMedia.prefersReducedTransparency === 'reduce',
                        '(update: fast)': _cssMedia.update === 'fast',
                        '(update: slow)': _cssMedia.update === 'slow',
                        '(update: none)': _cssMedia.update === 'none',
                    }};
                    // Also handle resolution
                    if (_cssMedia.resolution) {{
                        mediaMap['(min-resolution: 1dppx)'] = true;
                        mediaMap['(resolution: ' + _cssMedia.resolution + 'dppx)'] = true;
                    }}
                    window.matchMedia = function(query) {{
                        const q = query.trim();
                        if (mediaMap.hasOwnProperty(q)) {{
                            const result = origMatchMedia.call(window, q);
                            Object.defineProperty(result, 'matches', {{
                                get: () => mediaMap[q],
                                configurable: true
                            }});
                            return result;
                        }}
                        return origMatchMedia.call(window, q);
                    }};
                }}
            }} catch(e) {{}}

            // --- DOMRect fingerprint noise (seed-based) ---
            try {{
                const RECT_SEED = {canvas_seed} ^ 0xDEAD;
                function rectRng(seed) {{
                    return function() {{
                        seed |= 0; seed = seed + 0x6D2B79F5 | 0;
                        var t = Math.imul(seed ^ seed >>> 15, 1 | seed);
                        t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
                        return ((t ^ t >>> 14) >>> 0) / 4294967296;
                    }};
                }}
                const _rr = rectRng(RECT_SEED);
                // Per-profile tiny offset (0 to 0.001 px)
                const _rxOff = _rr() * 0.001;
                const _ryOff = _rr() * 0.001;
                const origGetBCR = Element.prototype.getBoundingClientRect;
                Element.prototype.getBoundingClientRect = function() {{
                    const r = origGetBCR.call(this);
                    return new DOMRect(r.x + _rxOff, r.y + _ryOff, r.width + _rxOff, r.height + _ryOff);
                }};
                const origGetCR = Element.prototype.getClientRects;
                Element.prototype.getClientRects = function() {{
                    const rects = origGetCR.call(this);
                    const out = [];
                    for (let i = 0; i < rects.length; i++) {{
                        const r = rects[i];
                        out.push(new DOMRect(r.x + _rxOff, r.y + _ryOff, r.width + _rxOff, r.height + _ryOff));
                    }}
                    return out;
                }};
            }} catch(e) {{}}

            // --- AudioContext fingerprint spoofing ---
            try {{
                const origCreateOscillator = AudioContext.prototype.createOscillator || 
                                              (typeof OfflineAudioContext !== 'undefined' && OfflineAudioContext.prototype.createOscillator);
                
                // Patch AnalyserNode.getFloatFrequencyData to add noise
                const origGetFloat = AnalyserNode.prototype.getFloatFrequencyData;
                AnalyserNode.prototype.getFloatFrequencyData = function(array) {{
                    origGetFloat.call(this, array);
                    // Add imperceptible noise to audio fingerprint data
                    for (let i = 0; i < array.length; i += 3) {{
                        array[i] = array[i] + (Math.random() * 0.0001 - 0.00005);
                    }}
                }};
                
                // Patch OfflineAudioContext.startRendering
                if (typeof OfflineAudioContext !== 'undefined') {{
                    const origStartRendering = OfflineAudioContext.prototype.startRendering;
                    OfflineAudioContext.prototype.startRendering = function() {{
                        return origStartRendering.call(this).then(function(buffer) {{
                            // Add noise to rendered audio buffer
                            const channel = buffer.getChannelData(0);
                            for (let i = 0; i < channel.length; i += 100) {{
                                channel[i] = channel[i] + (Math.random() * 0.0000001 - 0.00000005);
                            }}
                            return buffer;
                        }});
                    }};
                }}
            }} catch(e) {{}}

            // --- Plugins & MimeTypes: handled by enhanced mock below ---

            // --- Permissions API patch (all permission types) ---
            try {{
                const origQuery = Permissions.prototype.query;
                Permissions.prototype.query = function(params) {{
                    if (params && params.name) {{
                        // Return 'prompt' for privacy-sensitive permissions (matches real Chrome defaults)
                        const promptPerms = ['notifications', 'geolocation', 'camera', 'microphone',
                                             'midi', 'magnetometer', 'accelerometer', 'gyroscope',
                                             'clipboard-read', 'clipboard-write', 'speaker-selection',
                                             'display-capture', 'screen-wake-lock'];
                        if (promptPerms.includes(params.name)) {{
                            return Promise.resolve({{state: 'prompt', onchange: null}});
                        }}
                    }}
                    return origQuery.call(this, params);
                }};
            }} catch(e) {{}}

            // --- Speech Synthesis voices mock ---
            try {{
                const _voices = {_json.dumps(speech_voices)};
                if (_voices && _voices.length > 0) {{
                    const voiceObjects = _voices.map(v => {{
                        const obj = {{
                            name: v.name,
                            lang: v.lang.replace('_', '-'),
                            localService: v.localService !== false,
                            voiceURI: v.voiceURI || v.name,
                            default: v.default || false,
                        }};
                        return obj;
                    }});
                    Object.defineProperty(speechSynthesis, 'getVoices', {{
                        value: function() {{ return voiceObjects; }},
                        writable: false,
                        configurable: true,
                    }});
                    // Fire voiceschanged event on next tick
                    setTimeout(() => {{
                        try {{ speechSynthesis.dispatchEvent(new Event('voiceschanged')); }} catch(e) {{}}
                    }}, 50);
                }}
            }} catch(e) {{}}

            // --- Feature detection flags ---
            try {{
                const _feats = {_json.dumps(feature_flags)};
                if (_feats) {{
                    // Hide APIs that shouldn't exist on this device type
                    if (_feats.SharedWorker === false && typeof SharedWorker !== 'undefined') {{
                        Object.defineProperty(window, 'SharedWorker', {{ get: () => undefined, configurable: true }});
                    }}
                    if (_feats.WebHID === false && navigator.hid) {{
                        Object.defineProperty(Navigator.prototype, 'hid', {{ get: () => undefined, configurable: true }});
                    }}
                    if (_feats.Serial === false && navigator.serial) {{
                        Object.defineProperty(Navigator.prototype, 'serial', {{ get: () => undefined, configurable: true }});
                    }}
                    if (_feats.EyeDropperAPI === false && typeof EyeDropper !== 'undefined') {{
                        Object.defineProperty(window, 'EyeDropper', {{ get: () => undefined, configurable: true }});
                    }}
                    if (_feats.WebNFC === true && !('NDEFReader' in window)) {{
                        window.NDEFReader = function() {{ throw new DOMException('NFC not available', 'NotSupportedError'); }};
                    }}
                    if (_feats.ContactsManager === true && !navigator.contacts) {{
                        Object.defineProperty(Navigator.prototype, 'contacts', {{
                            get: () => ({{ select: () => Promise.resolve([]), getProperties: () => Promise.resolve(['name', 'email', 'tel']) }}),
                            configurable: true
                        }});
                    }}
                }}
            }} catch(e) {{}}

            // --- Audio properties override ---
            try {{
                const _audioProps = {_json.dumps(audio_props)};
                if (_audioProps && (_audioProps.BaseAudioContextSampleRate || _audioProps.sampleRate)) {{
                    const _sr = _audioProps.BaseAudioContextSampleRate || _audioProps.sampleRate;
                    const _bl = _audioProps.AudioContextBaseLatency || _audioProps.baseLatency || 0.01;
                    const _ol = _audioProps.AudioContextOutputLatency || _audioProps.outputLatency || 0;
                    const _mc = _audioProps.AudioDestinationNodeMaxChannelCount || _audioProps.maxChannelCount || 2;
                    const OrigAudioCtx = window.AudioContext || window.webkitAudioContext;
                    if (OrigAudioCtx) {{
                        const origProto = OrigAudioCtx.prototype;
                        Object.defineProperty(origProto, 'sampleRate', {{
                            get: function() {{ return _sr; }},
                            configurable: true
                        }});
                        Object.defineProperty(origProto, 'baseLatency', {{
                            get: function() {{ return _bl; }},
                            configurable: true
                        }});
                        Object.defineProperty(origProto, 'outputLatency', {{
                            get: function() {{ return _ol; }},
                            configurable: true
                        }});
                    }}
                    // Override destination maxChannelCount
                    try {{
                        Object.defineProperty(AudioDestinationNode.prototype, 'maxChannelCount', {{
                            get: function() {{ return _mc; }},
                            configurable: true
                        }});
                    }} catch(e2) {{}}
                }}
            }} catch(e) {{}}

            // --- Navigator.connection mock ---
            try {{
                const _connInfo = {_json.dumps(connection_info)};
                if (_connInfo && _connInfo.effectiveType) {{
                    const connObj = {{
                        effectiveType: _connInfo.effectiveType,
                        rtt: _connInfo.rtt || 50,
                        downlink: parseFloat(_connInfo.downlink) || 4.7,
                        saveData: _connInfo.saveData || false,
                        onchange: null,
                        addEventListener: function() {{}},
                        removeEventListener: function() {{}},
                        dispatchEvent: function() {{ return true; }},
                    }};
                    Object.defineProperty(Navigator.prototype, 'connection', {{
                        get: function() {{ return connObj; }},
                        configurable: true, enumerable: true
                    }});
                }}
            }} catch(e) {{}}

            // --- Storage quota mock ---
            try {{
                const _storageQuota = {storage_quota};
                if (navigator.storage && navigator.storage.estimate) {{
                    const origEstimate = navigator.storage.estimate.bind(navigator.storage);
                    navigator.storage.estimate = function() {{
                        return origEstimate().then(function(est) {{
                            est.quota = _storageQuota;
                            return est;
                        }}).catch(function() {{
                            return {{ quota: _storageQuota, usage: 0 }};
                        }});
                    }};
                }}
            }} catch(e) {{}}

            // --- Performance.memory (heap size) mock ---
            try {{
                const _heapSize = {heap_size};
                if (window.performance) {{
                    Object.defineProperty(performance, 'memory', {{
                        get: function() {{
                            return {{
                                jsHeapSizeLimit: _heapSize,
                                totalJSHeapSize: Math.floor(_heapSize * 0.6),
                                usedJSHeapSize: Math.floor(_heapSize * 0.4),
                            }};
                        }},
                        configurable: true, enumerable: true
                    }});
                }}
            }} catch(e) {{}}

            // --- Keyboard layout mock ---
            try {{
                const _kbKeys = {_json.dumps(keyboard_layout)};
                if (_kbKeys && _kbKeys.length > 0 && navigator.keyboard) {{
                    const origGetLayoutMap = navigator.keyboard.getLayoutMap;
                    navigator.keyboard.getLayoutMap = function() {{
                        return Promise.resolve({{
                            entries: function*() {{ for (const k of _kbKeys) yield [k, k]; }},
                            keys: function*() {{ for (const k of _kbKeys) yield k; }},
                            values: function*() {{ for (const k of _kbKeys) yield k; }},
                            get: function(key) {{ return _kbKeys.includes(key) ? key : undefined; }},
                            has: function(key) {{ return _kbKeys.includes(key); }},
                            forEach: function(cb) {{ _kbKeys.forEach(function(k) {{ cb(k, k); }}); }},
                            size: _kbKeys.length,
                        }});
                    }};
                }}
            }} catch(e) {{}}

            // --- MediaCapabilities (codecs) mock ---
            try {{
                const _codecs = {_json.dumps(codecs_list)};
                if (_codecs && _codecs.length > 0 && navigator.mediaCapabilities) {{
                    const origDecode = navigator.mediaCapabilities.decodingInfo.bind(navigator.mediaCapabilities);
                    navigator.mediaCapabilities.decodingInfo = function(config) {{
                        const ct = config && config.audio ? config.audio.contentType : (config && config.video ? config.video.contentType : '');
                        for (const c of _codecs) {{
                            if (ct && ct === c.contentType) {{
                                return Promise.resolve({{ supported: c.supported, smooth: c.smooth, powerEfficient: c.powerEfficient }});
                            }}
                        }}
                        return origDecode(config);
                    }};
                }}
            }} catch(e) {{}}

            // --- Fonts detection (canvas-based font fingerprinting defense) ---
            try {{
                const _profileFonts = {_json.dumps(fonts_list)};
                if (_profileFonts && _profileFonts.length > 0) {{
                    // Font fingerprinting works by measuring text width with different fonts
                    // We override measureText to give consistent results for profile fonts
                    const _fontSet = new Set(_profileFonts);
                    // Store reference for font detection scripts
                    window.__profileFonts = _fontSet;
                }}
            }} catch(e) {{}}

            // --- System colors override (getComputedStyle) ---
            try {{
                const _sysColors = {_json.dumps(system_colors)};
                if (_sysColors && Object.keys(_sysColors).length > 0) {{
                    const origGetComputed = window.getComputedStyle;
                    window.getComputedStyle = function(el, pseudo) {{
                        const result = origGetComputed.call(window, el, pseudo);
                        const origGetProp = result.getPropertyValue.bind(result);
                        result.getPropertyValue = function(prop) {{
                            if (_sysColors[prop]) return _sysColors[prop];
                            return origGetProp(prop);
                        }};
                        return result;
                    }};
                }}
            }} catch(e) {{}}

            // --- navigator.userAgentData mock ---
            try {{
                if (navigator.userAgentData) {{
                    const _brands = [
                        {{ brand: "Not/A)Brand", version: "8" }},
                        {{ brand: "Chromium", version: "{chrome_version.split('.')[0] if '.' in str(chrome_version) else '131'}" }},
                        {{ brand: "Google Chrome", version: "{chrome_version.split('.')[0] if '.' in str(chrome_version) else '131'}" }}
                    ];
                    Object.defineProperty(navigator, 'userAgentData', {{
                        get: function() {{
                            return {{
                                brands: _brands,
                                mobile: {'true' if is_mobile else 'false'},
                                platform: "{platform_name}",
                                getHighEntropyValues: function(hints) {{
                                    return Promise.resolve({{
                                        brands: _brands,
                                        mobile: {'true' if is_mobile else 'false'},
                                        platform: "{platform_name}",
                                        platformVersion: "15.0.0",
                                        architecture: "x86",
                                        bitness: "64",
                                        model: "",
                                        uaFullVersion: "{chrome_version}",
                                        fullVersionList: _brands.map(function(b) {{ return {{ brand: b.brand, version: "{chrome_version}" }}; }})
                                    }});
                                }},
                                toJSON: function() {{
                                    return {{ brands: _brands, mobile: {'true' if is_mobile else 'false'}, platform: "{platform_name}" }};
                                }}
                            }};
                        }},
                        configurable: true
                    }});
                }}
            }} catch(e) {{}}

            // --- Enhanced plugins/mimes mock ---
            try {{
                const pluginArr = [
                    {{ name: "PDF Viewer", filename: "internal-pdf-viewer", description: "Portable Document Format" }},
                    {{ name: "Chrome PDF Viewer", filename: "internal-pdf-viewer", description: "Portable Document Format" }},
                    {{ name: "Chromium PDF Viewer", filename: "internal-pdf-viewer", description: "Portable Document Format" }},
                    {{ name: "Microsoft Edge PDF Viewer", filename: "internal-pdf-viewer", description: "Portable Document Format" }},
                    {{ name: "WebKit built-in PDF", filename: "internal-pdf-viewer", description: "Portable Document Format" }}
                ];
                const mimeArr = [
                    {{ type: "application/pdf", suffixes: "pdf", description: "Portable Document Format" }},
                    {{ type: "text/pdf", suffixes: "pdf", description: "Portable Document Format" }}
                ];
                // Build fake PluginArray
                const fakePlugins = Object.create(PluginArray.prototype);
                pluginArr.forEach(function(p, i) {{
                    const plug = Object.create(Plugin.prototype);
                    Object.defineProperties(plug, {{
                        name: {{ value: p.name, enumerable: true }},
                        filename: {{ value: p.filename, enumerable: true }},
                        description: {{ value: p.description, enumerable: true }},
                        length: {{ value: 2, enumerable: true }},
                    }});
                    Object.defineProperty(fakePlugins, i, {{ value: plug, enumerable: true }});
                }});
                Object.defineProperty(fakePlugins, 'length', {{ value: pluginArr.length }});
                fakePlugins.item = function(i) {{ return fakePlugins[i] || null; }};
                fakePlugins.namedItem = function(n) {{
                    for (let i = 0; i < pluginArr.length; i++) {{ if (fakePlugins[i].name === n) return fakePlugins[i]; }}
                    return null;
                }};
                fakePlugins.refresh = function() {{}};
                Object.defineProperty(navigator, 'plugins', {{
                    get: function() {{ return fakePlugins; }},
                    configurable: true, enumerable: true
                }});
                // Build fake MimeTypeArray
                const fakeMimes = Object.create(MimeTypeArray.prototype);
                mimeArr.forEach(function(m, i) {{
                    const mime = Object.create(MimeType.prototype);
                    Object.defineProperties(mime, {{
                        type: {{ value: m.type, enumerable: true }},
                        suffixes: {{ value: m.suffixes, enumerable: true }},
                        description: {{ value: m.description, enumerable: true }},
                        enabledPlugin: {{ value: fakePlugins[0], enumerable: true }},
                    }});
                    Object.defineProperty(fakeMimes, i, {{ value: mime, enumerable: true }});
                }});
                Object.defineProperty(fakeMimes, 'length', {{ value: mimeArr.length }});
                fakeMimes.item = function(i) {{ return fakeMimes[i] || null; }};
                fakeMimes.namedItem = function(t) {{
                    for (let i = 0; i < mimeArr.length; i++) {{ if (fakeMimes[i].type === t) return fakeMimes[i]; }}
                    return null;
                }};
                Object.defineProperty(navigator, 'mimeTypes', {{
                    get: function() {{ return fakeMimes; }},
                    configurable: true, enumerable: true
                }});
                // Also define pdfViewerEnabled
                Object.defineProperty(navigator, 'pdfViewerEnabled', {{
                    get: function() {{ return true; }},
                    configurable: true
                }});
            }} catch(e) {{}}

            // --- Chrome runtime mock ---
            try {{
                if (!window.chrome) window.chrome = {{}};
                if (!window.chrome.runtime) {{
                    window.chrome.runtime = {{
                        connect: function() {{}},
                        sendMessage: function() {{}}
                    }};
                }}
            }} catch(e) {{}}

            // --- Prevent iframe contentWindow detection ---
            try {{
                const origContentWindow = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow');
                Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {{
                    get: function() {{
                        const win = origContentWindow.get.call(this);
                        if (win) {{
                            try {{
                                // Shadow the iframe's navigator.webdriver too
                                Object.defineProperty(win.navigator, 'webdriver', {{
                                    get: () => undefined,
                                    configurable: true
                                }});
                            }} catch(e) {{}}
                        }}
                        return win;
                    }},
                    configurable: true,
                    enumerable: true
                }});
            }} catch(e) {{}}
            """

            # Inject via CDP — runs BEFORE page JS on every navigation
            driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                'source': stealth_script
            })
            logger.info("✅ Stealth fingerprint scripts injected via CDP (pre-page-load)")

        except Exception as e:
            logger.error(f"Error injecting fingerprint scripts via CDP: {e}")
            # Fallback: inject via execute_script (less reliable but better than nothing)
            try:
                driver.execute_script(stealth_script)
                logger.warning("⚠️ Fingerprint scripts injected via execute_script (fallback)")
            except Exception as e2:
                logger.error(f"Fallback fingerprint injection also failed: {e2}")

    def navigate_to_url(self, browser_id: str, url: str, timeout: int = 30) -> bool:
        """Navigate browser to specified URL."""
        try:
            if browser_id not in self.active_browsers:
                raise ValueError(f"Browser session {browser_id} not found")

            driver = self.active_browsers[browser_id]
            driver.set_page_load_timeout(timeout)

            try:
                driver.get(url)
            except TimeoutException:
                logger.warning(f"Page load timeout ({timeout}s) for {url}, checking if page is usable...")
                try:
                    state = driver.execute_script("return document.readyState")
                    current = driver.current_url
                    if state in ("interactive", "complete") and current and current != "about:blank" and current != "data:,":
                        logger.info(f"Page is usable (readyState={state}, url={current[:100]})")
                        return True
                except Exception:
                    pass
                logger.error(f"Timeout navigating {browser_id} to {url} — page not usable")
                return False

            logger.info(f"Successfully navigated {browser_id} to {url}")
            return True

        except TimeoutException:
            try:
                driver = self.active_browsers.get(browser_id)
                if driver:
                    current = driver.current_url
                    if current and current != "about:blank" and current != "data:,":
                        logger.warning(f"ReadyState wait timed out but URL changed to {current[:100]} — treating as success")
                        return True
            except Exception:
                pass
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

            if not actions:
                actions = ["scroll", "mouse_move", "click_random"]

            for action in actions:
                try:
                    if action == "scroll":
                        self._perform_scroll(driver)
                    elif action == "mouse_move":
                        self._perform_mouse_movement(driver)
                    elif action == "click_random":
                        self._perform_random_click(driver)
                    elif action == "type_text":
                        self._perform_typing(driver)

                    time.sleep(random.uniform(0.2, 0.5))

                except Exception as e:
                    logger.warning(f"Error performing action {action}: {e}")
                    continue

            return True

        except Exception as e:
            logger.error(f"Error performing human actions in {browser_id}: {e}")
            return False

    def _perform_scroll(self, driver: PlaywrightDriver):
        """Human-like smooth scrolling."""
        max_scrolls = random.randint(2, 4)

        for _ in range(max_scrolls):
            scroll_distance = random.randint(150, 500)
            steps = random.randint(3, 8)
            step_size = scroll_distance // steps
            for i in range(steps):
                driver.execute_script(f"window.scrollBy({{top: {step_size}, behavior: 'smooth'}});")
                time.sleep(random.uniform(0.02, 0.08))
            time.sleep(random.uniform(0.3, 1.2))

    def _perform_mouse_movement(self, driver: PlaywrightDriver):
        """Human-like mouse movement."""
        try:
            viewport_width = driver.execute_script("return window.innerWidth")
            viewport_height = driver.execute_script("return window.innerHeight")

            page = driver._page
            # Start from center
            cx, cy = viewport_width // 2, viewport_height // 2
            page.mouse.move(cx, cy)
            time.sleep(random.uniform(0.05, 0.15))

            for _ in range(random.randint(1, 3)):
                target_x = random.randint(viewport_width // 6, viewport_width * 5 // 6)
                target_y = random.randint(viewport_height // 6, viewport_height * 5 // 6)

                # Move in small steps (Bezier-like)
                steps = random.randint(5, 15)
                start_x, start_y = cx, cy
                for step in range(steps):
                    t = (step + 1) / steps
                    ease = t * t * (3 - 2 * t)
                    ix = int(start_x + (target_x - start_x) * ease)
                    iy = int(start_y + (target_y - start_y) * ease)
                    try:
                        page.mouse.move(ix, iy)
                    except Exception:
                        break
                    time.sleep(random.uniform(0.005, 0.02))

                cx, cy = target_x, target_y
                time.sleep(random.uniform(0.1, 0.4))
        except Exception as e:
            logger.warning(f"Mouse movement error: {e}")

    def _perform_random_click(self, driver: PlaywrightDriver):
        """Click on random safe elements."""
        try:
            clickable_elements = driver.find_elements(
                By.CSS_SELECTOR,
                "a, button, div[onclick], span[onclick], [role='button']"
            )

            if clickable_elements:
                safe_elements = []
                for element in clickable_elements:
                    try:
                        if element.is_displayed() and element.is_enabled():
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
                        box = element._element_handle.bounding_box()
                        if box:
                            page = driver._page
                            page.mouse.move(
                                box['x'] + box['width'] / 2,
                                box['y'] + box['height'] / 2
                            )
                            time.sleep(random.uniform(0.05, 0.2))
                            page.mouse.click(
                                box['x'] + box['width'] / 2,
                                box['y'] + box['height'] / 2
                            )
                        time.sleep(random.uniform(0.3, 0.8))
                    except:
                        pass

        except Exception as e:
            logger.warning(f"Error performing random click: {e}")

    def _perform_typing(self, driver: PlaywrightDriver):
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
                        input_type = element.get_attribute("type") or ""
                        name = element.get_attribute("name") or ""
                        if input_type not in ["password", "email"] and "password" not in name.lower():
                            safe_inputs.append(element)
                except:
                    continue

            if safe_inputs:
                element = random.choice(safe_inputs)
                try:
                    texts = ["test", "hello", "search", "example", "demo"]
                    text = random.choice(texts)

                    element.clear()
                    for char in text:
                        element.send_keys(char)
                        time.sleep(random.uniform(0.1, 0.3))

                    time.sleep(random.uniform(1, 2))
                    element.send_keys(Keys.ESCAPE)
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
        """Close browser session and clean up Chrome processes."""
        pids = self.browser_pids.pop(browser_id, {})
        chrome_pid = pids.get('chrome_pid')
        profile_dir = pids.get('profile_dir', '')
        
        logger.info(f"🔒 Closing browser {browser_id} (chrome_pid={chrome_pid}, dir={profile_dir})")
        
        # Step 1: Try graceful close via Playwright
        try:
            if browser_id in self.active_browsers:
                driver = self.active_browsers[browser_id]
                try:
                    driver.quit()
                except Exception as quit_error:
                    logger.warning(f"driver.quit() failed for {browser_id}: {quit_error}")
        except Exception as e:
            logger.warning(f"Error during graceful close for {browser_id}: {e}")
        
        # Step 2: Kill Chrome process tree by PID
        if chrome_pid:
            _kill_process_tree(chrome_pid)
        
        # Step 3: Kill ALL Chrome processes by profile directory
        if profile_dir:
            self._kill_chrome_by_profile_dir(profile_dir)
        
        # Step 4: Remove stale SingletonLock
        if profile_dir:
            singleton_lock = os.path.join(profile_dir, "SingletonLock")
            if os.path.exists(singleton_lock) or os.path.islink(singleton_lock):
                try:
                    os.remove(singleton_lock)
                    logger.info(f"🗑️ Cleaned up SingletonLock in {os.path.basename(profile_dir)}")
                except OSError:
                    pass

        # Step 5: Cleanup dictionaries
        self.active_browsers.pop(browser_id, None)
        self.browser_profiles.pop(browser_id, None)
        
        logger.info(f"✅ Browser session {browser_id} fully closed")

    def _kill_chrome_by_profile_dir(self, profile_dir: str):
        """Find and kill ALL Chrome processes that use a specific profile directory."""
        killed = 0
        try:
            import psutil
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    name = (proc.info.get('name') or '').lower()
                    if 'chrome' not in name and 'chromedriver' not in name:
                        continue
                    cmdline = ' '.join(proc.info.get('cmdline') or [])
                    if profile_dir in cmdline:
                        proc.kill()
                        killed += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
            if killed:
                logger.info(f"🔪 Killed {killed} Chrome processes for {os.path.basename(profile_dir)}")
        except ImportError:
            # psutil not available — use pkill
            try:
                subprocess.run(
                    ['pkill', '-9', '-f', profile_dir],
                    capture_output=True, timeout=5
                )
            except:
                pass
        except Exception as e:
            logger.warning(f"Error killing Chrome by profile dir: {e}")

    def close_all_sessions(self):
        """Close all active browser sessions and cleanup orphans."""
        browser_ids = list(self.active_browsers.keys())
        for browser_id in browser_ids:
            self.close_browser_session(browser_id)

        cleanup_orphaned_chrome()
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