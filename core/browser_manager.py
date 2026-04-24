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

# ============================================================================
# Browser backend A/B testing (rebrowser-playwright vs patchright)
# ----------------------------------------------------------------------------
# Two singletons live side-by-side in the same process. Backend is chosen
# per profile via deterministic hash so the same profile always uses the
# same backend — making per-profile success metrics directly comparable.
# ============================================================================
import hashlib as _hashlib
import threading as _threading

BACKEND_REBROWSER = "rebrowser"
BACKEND_PATCHRIGHT = "patchright"

_playwright_instances: Dict[str, Playwright] = {}
_playwright_lock = _threading.Lock()

# Lazy redis client (created on first counter increment)
_redis_client = None
_redis_lock = _threading.Lock()


def _get_redis():
    """Lazy-init Redis client for backend metrics counters."""
    global _redis_client
    if _redis_client is None:
        with _redis_lock:
            if _redis_client is None:
                try:
                    import redis
                    _redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
                except Exception as e:
                    logger.debug(f"Redis unavailable for backend metrics: {e}")
                    _redis_client = False  # sentinel — don't retry
    return _redis_client if _redis_client else None


def _bump_backend_metric(backend: str, event: str) -> None:
    """Increment Redis counter `backend:{backend}:{event}` (best-effort).

    Events: launch_ok, launch_fail, session_close_ok, session_close_err.
    Used by _check_ab.py to compare patchright vs rebrowser performance.
    """
    try:
        r = _get_redis()
        if r:
            r.incr(f"backend:{backend}:{event}")
    except Exception:
        pass


def _pick_backend(profile_name: str) -> str:
    """Sticky per-profile backend selection.

    Hash the profile name to a 0-99 bucket, route to patchright if bucket
    falls under `browser_backend_patchright_pct`. Sticky means the same
    profile always lands on the same backend across restarts.
    """
    pct = max(0, min(100, int(getattr(settings, "browser_backend_patchright_pct", 0))))
    if pct <= 0:
        return BACKEND_REBROWSER
    if pct >= 100:
        return BACKEND_PATCHRIGHT
    bucket = int(_hashlib.md5(profile_name.encode()).hexdigest()[:8], 16) % 100
    return BACKEND_PATCHRIGHT if bucket < pct else BACKEND_REBROWSER


# Shared Playwright instance (reused across BrowserManager instances within the same process)
_playwright_instance: Optional[Playwright] = None  # legacy alias — kept for backwards compat

# Directory for proxy auth Chrome extensions (one per proxy, reused across sessions)
_PROXY_AUTH_EXT_DIR = os.path.join(tempfile.gettempdir(), 'pw_proxy_auth_extensions')


def _get_playwright(backend: str = BACKEND_REBROWSER) -> Playwright:
    """Get or create a Playwright instance for the given backend.

    rebrowser → `from playwright.sync_api import sync_playwright`
                 (works because Dockerfile symlinks rebrowser_playwright as playwright)
    patchright → `from patchright.sync_api import sync_playwright`
                 (separate package, separate driver, separate bundled chromium)
    """
    if backend not in (BACKEND_REBROWSER, BACKEND_PATCHRIGHT):
        backend = BACKEND_REBROWSER
    if backend in _playwright_instances:
        return _playwright_instances[backend]
    with _playwright_lock:
        if backend in _playwright_instances:
            return _playwright_instances[backend]
        # IMPORTANT: sync_playwright keeps a background asyncio loop alive
        # via greenlet. Starting BOTH rebrowser and patchright sync clients
        # in the same process causes the second one to crash with
        # "Playwright Sync API inside the asyncio loop". So once a backend
        # is started in this process, ALL subsequent calls return that one
        # (the routing is sticky per profile, but a single celery worker
        # serves many profiles sequentially — first wins for that worker).
        if _playwright_instances:
            existing_backend = next(iter(_playwright_instances))
            if existing_backend != backend:
                logger.info(
                    f"⚓ Backend pinned to '{existing_backend}' for this process "
                    f"(asked for '{backend}', but sync_playwright is process-exclusive)"
                )
                _bump_backend_metric(backend, "fallback_to_pinned")
                return _playwright_instances[existing_backend]
        if backend == BACKEND_PATCHRIGHT:
            try:
                from patchright.sync_api import sync_playwright as _patch_sp
                _playwright_instances[backend] = _patch_sp().start()
                logger.info("✅ Patchright instance started (backend=patchright)")
            except ImportError:
                logger.warning("⚠️ patchright not installed — falling back to rebrowser")
                backend = BACKEND_REBROWSER
            except Exception as e:
                logger.error(f"❌ Patchright failed to start: {e} — falling back to rebrowser")
                backend = BACKEND_REBROWSER
        if backend == BACKEND_REBROWSER and backend not in _playwright_instances:
            _playwright_instances[backend] = sync_playwright().start()
            logger.info("✅ Playwright (rebrowser) instance started")
        return _playwright_instances[backend]


def _create_proxy_auth_extension(host: str, port: str, username: str, password: str) -> str:
    """Create a Chrome extension that handles proxy authentication.
    
    Some proxies respond to unauthenticated CONNECT with 407 + Connection:close,
    which breaks Playwright's Fetch-based auth (can't retry on closed connection).
    This extension uses chrome.webRequest.onAuthRequired to provide credentials
    natively, bypassing the issue entirely.
    
    Returns the path to the extension directory (reused if already exists for same proxy).
    """
    import hashlib
    ext_id = hashlib.md5(f"{host}:{port}:{username}".encode()).hexdigest()[:12]
    ext_dir = os.path.join(_PROXY_AUTH_EXT_DIR, f"proxy_auth_{ext_id}")
    
    manifest_path = os.path.join(ext_dir, "manifest.json")
    bg_path = os.path.join(ext_dir, "background.js")
    
    # Reuse existing extension if it already exists
    if os.path.exists(manifest_path) and os.path.exists(bg_path):
        return ext_dir
    
    os.makedirs(ext_dir, exist_ok=True)
    
    manifest = {
        "version": "1.0.0",
        "manifest_version": 2,
        "name": "Proxy Auth Helper",
        "permissions": ["proxy", "webRequest", "webRequestBlocking", "<all_urls>"],
        "background": {"scripts": ["background.js"]},
        "minimum_chrome_version": "76.0.0"
    }
    
    # Escape special chars in credentials for JS string literals
    safe_user = username.replace("\\", "\\\\").replace("'", "\\'")
    safe_pass = password.replace("\\", "\\\\").replace("'", "\\'")
    
    background_js = f"""
chrome.webRequest.onAuthRequired.addListener(
    function(details) {{
        return {{
            authCredentials: {{
                username: '{safe_user}',
                password: '{safe_pass}'
            }}
        }};
    }},
    {{urls: ['<all_urls>']}},
    ['blocking']
);
""".strip()
    
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)
    with open(bg_path, 'w') as f:
        f.write(background_js)
    
    logger.info(f"🔐 Created proxy auth extension at {ext_dir}")
    return ext_dir


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
    """Kill Chrome processes whose owning Celery worker is dead.
    
    When a ForkPoolWorker is SIGKILL'd, its Playwright node-driver and Chrome
    children survive (reparented to celery main process or init). This function
    detects them by checking the full parent chain:
      Chrome → node-driver → ???
    If the chain leads to a LIVE ForkPoolWorker, Chrome is active.
    If it leads to the celery MainProcess, init, or a dead PID — it's orphaned.
    """
    killed = 0
    try:
        import psutil
        
        # Build set of live ForkPoolWorker PIDs
        live_worker_pids = set()
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = ' '.join(proc.info.get('cmdline') or [])
                # ForkPoolWorker processes have 'celery' in cmdline and are children of main
                if 'celery' in cmdline.lower() and 'ForkPoolWorker' in (proc.name() or ''):
                    live_worker_pids.add(proc.info['pid'])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        
        # If we can't detect workers by name, try by process tree
        if not live_worker_pids:
            for proc in psutil.process_iter(['pid', 'cmdline']):
                try:
                    cmdline = ' '.join(proc.info.get('cmdline') or [])
                    if 'celery' in cmdline.lower():
                        p = psutil.Process(proc.info['pid'])
                        # Workers are children of the main celery process
                        # Main process has ppid = docker-init (1) or entrypoint
                        # Workers have ppid = main celery process
                        parent = p.parent()
                        if parent and 'celery' in ' '.join(parent.cmdline()).lower():
                            live_worker_pids.add(proc.info['pid'])
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        
        logger.info(f"🔍 cleanup_orphaned_chrome: {len(live_worker_pids)} live workers detected")
        
        # Find node-driver processes whose parent worker is dead
        orphan_node_pids = set()
        for proc in psutil.process_iter(['pid', 'cmdline', 'ppid']):
            try:
                cmdline = ' '.join(proc.info.get('cmdline') or [])
                if 'run-driver' not in cmdline:
                    continue
                # This is a Playwright node-driver. Its parent should be a live worker.
                ppid = proc.info.get('ppid', 0)
                if ppid not in live_worker_pids:
                    orphan_node_pids.add(proc.info['pid'])
                    logger.info(f"🧹 Orphaned node-driver PID={proc.info['pid']} (parent {ppid} not a live worker)")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        
        # Kill all orphaned node-drivers and their entire process trees (Chrome included)
        for node_pid in orphan_node_pids:
            try:
                parent = psutil.Process(node_pid)
                children = parent.children(recursive=True)
                for child in children:
                    try:
                        child.kill()
                        killed += 1
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                try:
                    parent.kill()
                    killed += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                psutil.wait_procs(children + [parent], timeout=3)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        
        # Also kill any Chrome with ppid=1 (truly orphaned, no parent at all)
        for proc in psutil.process_iter(['pid', 'name', 'ppid']):
            try:
                name = (proc.info.get('name') or '').lower()
                if 'chrome' not in name:
                    continue
                if proc.info.get('ppid', 0) <= 1:
                    try:
                        proc.kill()
                        killed += 1
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
                
    except ImportError:
        # psutil not available — fallback
        try:
            subprocess.run(['pkill', '-9', '-f', 'chrome.*--no-sandbox'], capture_output=True, timeout=5)
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"Error in cleanup_orphaned_chrome: {e}")
    if killed:
        logger.info(f"🧹 Cleaned up {killed} orphaned Chrome/node-driver processes")
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

            # ---- A/B backend selection (sticky per profile name) ----
            # Select BEFORE computing profile_dir — patchright uses an isolated
            # subdir so its (older) Chromium 136 doesn't read profile data
            # written by rebrowser's Chrome 145 (incompatible Preferences/Local
            # Storage formats crash the renderer at Page.enable).
            requested_backend = _pick_backend(profile_data.get("name", ""))
            playwright = _get_playwright(requested_backend)
            # Process may already be pinned to a different backend (sync_playwright
            # is process-exclusive — see _get_playwright). Read back the pinned one.
            backend = next(iter(_playwright_instances), requested_backend)
            profile_data["_backend"] = backend  # downstream tasks/logs can read this
            if backend == requested_backend:
                logger.info(f"🔀 Backend selected for {profile_data.get('name','?')}: {backend}")
            else:
                logger.info(
                    f"🔀 Backend for {profile_data.get('name','?')}: requested={requested_backend} "
                    f"served={backend} (process pinned)"
                )

            # Per-backend profile path. rebrowser keeps the historical path
            # (preserves warmup/cookies). patchright gets an isolated subdir
            # — first run is fresh; warmup will rebuild it for that backend.
            base_profile_dir = os.path.abspath(os.path.join(settings.browser_user_data_dir, profile_data["name"]))
            if backend == BACKEND_PATCHRIGHT:
                profile_dir = os.path.join(base_profile_dir, "_patchright")
                os.makedirs(profile_dir, exist_ok=True)
            else:
                profile_dir = base_profile_dir
            profile_data["_profile_dir"] = profile_dir

            # Repair profile directory
            self.repair_profile_dir(profile_dir)
            singleton_lock = os.path.join(profile_dir, "SingletonLock")
            if os.path.exists(singleton_lock) or os.path.islink(singleton_lock):
                try:
                    os.remove(singleton_lock)
                    logger.warning(f"🗑️ Removed stale SingletonLock for {profile_data['name']}")
                except OSError as e:
                    logger.warning(f"Could not remove SingletonLock: {e}")

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
                # Use full Chromium binary (not headless_shell) to avoid TLS/JA3
                # fingerprint detection by SmartCaptcha. headless_shell has a unique
                # TLS fingerprint that Yandex's server detects, triggering captcha
                # on 100% of search requests.
                import glob
                if backend == BACKEND_PATCHRIGHT:
                    # patchright bundles its own patched chromium — MUST use it,
                    # because patchright applies binary-level patches that other
                    # chromium builds don't have. Glob picks newest patchright build.
                    patch_paths = sorted(glob.glob('/opt/pw-browsers/chromium-*/chrome-linux/chrome'))
                    chromium_exe = patch_paths[-1] if patch_paths else None
                    if not chromium_exe:
                        # Fallback to chrome-linux64 layout if patchright happens to use it
                        patch_paths = sorted(glob.glob('/opt/pw-browsers/chromium-*/chrome-linux64/chrome'))
                        chromium_exe = patch_paths[-1] if patch_paths else None
                else:
                    chromium_paths = sorted(glob.glob('/opt/pw-browsers/chromium-*/chrome-linux*/chrome'))
                    chromium_exe = chromium_paths[-1] if chromium_paths else None

                # Launch persistent context (uses profile directory for cookies/storage)
                # timeout=60s prevents hanging on slow proxy handshake / corrupted profile
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=profile_dir,
                    headless=False,  # Always headed — uses Xvfb virtual display
                    executable_path=chromium_exe,
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
                    timeout=60000,  # 60s max for Chrome launch (prevents hang on proxy/profile issues)
                )
            except Exception as launch_exc:
                logger.warning(f"Chrome launch failed, cleaning up orphans for {profile_dir}: {launch_exc}")
                _bump_backend_metric(backend, "launch_fail")
                self._kill_chrome_by_profile_dir(profile_dir)
                singleton_lock = os.path.join(profile_dir, "SingletonLock")
                if os.path.exists(singleton_lock) or os.path.islink(singleton_lock):
                    try:
                        os.remove(singleton_lock)
                    except OSError:
                        pass
                raise launch_exc

            _bump_backend_metric(backend, "launch_ok")
            logger.info(f"✅ Browser created successfully (backend={backend})")

            # Apply playwright-stealth BEFORE our custom fingerprint scripts.
            # SKIP on patchright: patchright already applies overlapping anti-detect
            # patches at the driver level, and stacking playwright-stealth on top
            # causes double-patching warnings + can re-introduce signals patchright
            # specifically removed (e.g. duplicate Function.prototype.toString hooks).
            if backend == BACKEND_PATCHRIGHT:
                logger.info("⏭️  playwright-stealth SKIPPED (patchright handles this internally)")
            else:
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
                        sec_ch_ua=False,  # DISABLED: we set YaBrowser brands ourselves via CDP + JS
                        # Features we already handle with profile-specific values — DISABLED:
                        navigator_webdriver=True,       # ENABLED: double protection with our CDP script
                        navigator_hardware_concurrency=False,  # profile-specific
                        navigator_languages=False,       # profile-specific (ru-RU)
                        navigator_languages_override=None,  # silence "override provided but feature disabled" warning
                        navigator_platform=False,        # profile-specific
                        navigator_platform_override=None,  # silence "override provided but feature disabled" warning
                        navigator_plugins=False,         # our custom mock
                        navigator_permissions=False,     # our custom patch
                        navigator_user_agent=False,      # set via CDP
                        chrome_runtime=True,             # SmartCaptcha checks window.chrome.runtime
                        iframe_content_window=True,      # SmartCaptcha checks iframe access
                        webgl_vendor=False,              # profile-specific values
                    )
                    stealth.apply_stealth_sync(context)
                    logger.info("✅ playwright-stealth applied (chrome_app, chrome_csi, chrome_load_times, hairline, media_codecs, error_prototype, navigator_vendor, sec_ch_ua, chrome_runtime, iframe_content_window)")
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
            pids = {'chrome_pid': None, 'node_driver_pid': None, 'profile_dir': profile_dir}
            try:
                pid = driver.browser_pid
                if pid:
                    pids['chrome_pid'] = pid
            except Exception:
                pass
            try:
                nd_pid = driver.node_driver_pid
                if nd_pid:
                    pids['node_driver_pid'] = nd_pid
            except Exception:
                pass
            self.browser_pids[browser_id] = pids

            logger.info(f"Created browser session: {browser_id} (chrome_pid={pids['chrome_pid']}, node_driver={pids['node_driver_pid']})")
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
        js_heap_mb = os.environ.get('YANDEX_BOT_BROWSER_JS_HEAP', '1024')
        args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            # NOTE: --disable-blink-features=AutomationControlled was removed.
            # The flag itself is invisible from JS, BUT it disables a feature
            # that real Chrome users have ENABLED. Some fingerprinters check
            # whether AutomationControlled-related APIs behave as expected and
            # flag the absence as a bot signal. We rely solely on the JS
            # `navigator.webdriver` patch + Page.addScriptToEvaluateOnNewDocument.
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-hang-monitor",
            f"--js-flags=--max-old-space-size={js_heap_mb}",
            "--disable-ipc-flooding-protection",
            # WebRTC: prevent real IP leak through STUN/TURN
            "--enforce-webrtc-ip-permission-check",
            "--webrtc-ip-handling-policy=disable_non_proxied_udp",
            # WebGL: use SwiftShader for software rendering (no GPU on server)
            "--use-gl=angle",
            "--use-angle=swiftshader",
            "--enable-unsafe-swiftshader",
        ]
        
        from app.config import settings as _settings

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
                ya_match = re.search(r'YaBrowser/(\d+)\.(\d+)\.(\d+)\.(\d+)', ua_str)
                if chrome_match:
                    major_ver = chrome_match.group(1)
                    full_ver = chrome_match.group(0).replace('Chrome/', '')
                    
                    # Extract YaBrowser version if present
                    ya_major = ya_match.group(1) if ya_match else major_ver
                    ya_full = ya_match.group(0).replace('YaBrowser/', '') if ya_match else full_ver
                    
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
                            platform_ver = '10.0.0'
                        elif 'Macintosh' in ua_str or 'Mac OS X' in ua_str:
                            ua_platform = 'macOS'
                            platform_ver = '14.7.6'
                        else:
                            ua_platform = 'Linux'
                            platform_ver = '6.5.0'
                        architecture = 'x86' if 'x86' in ua_str or 'Win' in ua_str else 'arm'
                        bitness = '64'
                    
                    # Build brands list: YaBrowser format if YaBrowser UA, else Chrome
                    if ya_match:
                        brands = [
                            {'brand': 'Chromium', 'version': major_ver},
                            {'brand': 'YaBrowser', 'version': ya_major},
                            {'brand': 'Yowser', 'version': '2'},
                            {'brand': 'Not_A Brand', 'version': ya_major}
                        ]
                        full_version_list = [
                            {'brand': 'Chromium', 'version': full_ver},
                            {'brand': 'YaBrowser', 'version': ya_full},
                            {'brand': 'Yowser', 'version': '2.5'},
                            {'brand': 'Not_A Brand', 'version': ya_full}
                        ]
                    else:
                        brands = [
                            {'brand': 'Chromium', 'version': major_ver},
                            {'brand': 'Google Chrome', 'version': major_ver},
                            {'brand': 'Not-A.Brand', 'version': '99'}
                        ]
                        full_version_list = [
                            {'brand': 'Chromium', 'version': full_ver},
                            {'brand': 'Google Chrome', 'version': full_ver},
                            {'brand': 'Not-A.Brand', 'version': '99.0.0.0'}
                        ]
                    
                    driver.execute_cdp_cmd('Emulation.setUserAgentOverride', {
                        'userAgent': ua_str,
                        'platform': profile_data.get('platform', 'Linux armv81' if is_mobile else 'Win32'),
                        'userAgentMetadata': {
                            'brands': brands,
                            'fullVersionList': full_version_list,
                            'fullVersion': ya_full if ya_match else full_ver,
                            'platform': ua_platform,
                            'platformVersion': platform_ver,
                            'architecture': architecture if is_mobile else ('x86' if 'x86' in ua_str or 'Win' in ua_str else 'arm'),
                            'model': model,
                            'mobile': is_mobile,
                            'bitness': bitness if is_mobile else '64',
                            'wow64': False
                        }
                    })
                    browser_label = f'YaBrowser/{ya_major}' if ya_match else f'Chrome/{major_ver}'
                    device_label = f"📱 {model}" if is_mobile else f"🖥️ {ua_platform}"
                    logger.info(f"🛡️ userAgentData override set: {browser_label} {device_label}")
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
            is_mobile = profile_data.get("is_mobile", False)
            hw_concurrency = profile_data.get('hardware_concurrency', 8 if is_mobile else 4)
            dev_memory = profile_data.get('device_memory', 8)
            platform = profile_data.get("platform", "Win32")
            max_touch = profile_data.get('max_touch_points', 5 if is_mobile else 0)
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

            # V8 heap flag value (must match --max-old-space-size in launch args)
            js_heap_mb = int(os.environ.get('YANDEX_BOT_BROWSER_JS_HEAP', '1024'))

            # Extract chrome and YaBrowser versions from user_agent for userAgentData mock
            import re as _re
            _ua = profile_data.get("user_agent", "")
            _cv_match = _re.search(r'Chrome/(\d+[\.\d]*)', _ua)
            chrome_version = _cv_match.group(1) if _cv_match else "145.0.7632.6"
            _ya_match = _re.search(r'YaBrowser/(\d+[\.\d]*)', _ua)
            ya_version = _ya_match.group(1) if _ya_match else ""
            ya_major = ya_version.split('.')[0] if ya_version else ""
            is_yabrowser = bool(_ya_match)
            # Platform name + platform_version + architecture for userAgentData
            # MUST match the actual UA string — mismatch is a strong bot signal.
            _plat = profile_data.get("platform", "Win32")
            _ua_lower = _ua.lower()
            if is_mobile or 'android' in _ua_lower:
                platform_name = "Android"
                _mob_dev = profile_data.get('mobile_device', {}) or {}
                ua_platform_version = (_mob_dev.get('android') or '14') + '.0.0'
                ua_architecture = ''   # Real Android Chrome returns empty string
                ua_bitness = ''
                ua_model = _mob_dev.get('model', '')
            elif "Win" in _plat or 'windows' in _ua_lower:
                platform_name = "Windows"
                ua_platform_version = "15.0.0"  # Win11 reports as 15.x via UA-CH
                ua_architecture = "x86"
                ua_bitness = "64"
                ua_model = ""
            elif "Mac" in _plat or 'mac os x' in _ua_lower or 'macintosh' in _ua_lower:
                platform_name = "macOS"
                ua_platform_version = "14.7.0"
                ua_architecture = "arm" if 'arm' in _ua_lower else "x86"
                ua_bitness = "64"
                ua_model = ""
            elif "Linux" in _plat or 'linux' in _ua_lower:
                platform_name = "Linux"
                ua_platform_version = "6.5.0"
                ua_architecture = "x86"
                ua_bitness = "64"
                ua_model = ""
            else:
                platform_name = "Windows"
                ua_platform_version = "15.0.0"
                ua_architecture = "x86"
                ua_bitness = "64"
                ua_model = ""

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

            // --- Utility: make patched functions appear native ---
            // SmartCaptcha checks Function.prototype.toString() to detect overrides.
            const _nativeToStringFunc = Function.prototype.toString;
            const _nativeFuncs = new Map();
            function _maskAsNative(patched, originalName) {{
                _nativeFuncs.set(patched, `function ${{originalName || patched.name || ''}}() {{ [native code] }}`);
            }}
            // Patch Function.prototype.toString once
            try {{
                Function.prototype.toString = function() {{
                    if (_nativeFuncs.has(this)) return _nativeFuncs.get(this);
                    return _nativeToStringFunc.call(this);
                }};
                _maskAsNative(Function.prototype.toString, 'toString');
            }} catch(e) {{}}

            // --- Remove webdriver flag ---
            // Real Chrome sets navigator.webdriver = false (not undefined!)
            // Must override on prototype so iframes inherit the value.
            // Delete first, then redefine — prevents Playwright/CDP from re-enabling.
            try {{
                // Delete existing property on prototype
                delete Navigator.prototype.webdriver;
                // Also delete on instance if present
                if ('webdriver' in navigator) {{
                    delete navigator.webdriver;
                }}
                // Redefine on prototype with getter returning false
                const _webdriverGetter = function() {{ return false; }};
                _maskAsNative(_webdriverGetter, 'get webdriver');
                Object.defineProperty(Navigator.prototype, 'webdriver', {{
                    get: _webdriverGetter,
                    configurable: true,
                    enumerable: true
                }});
                // Also define on instance for extra safety
                Object.defineProperty(navigator, 'webdriver', {{
                    get: _webdriverGetter,
                    configurable: true,
                    enumerable: true
                }});
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
                // outerWidth/outerHeight: real Chrome has
                //   outerWidth  ≈ innerWidth  (same content area width)
                //   outerHeight ≈ innerHeight + ~87px (title bar + tabs + address bar)
                // Setting them equal to screen.width/height (as before) is impossible
                // for any windowed browser and is a known bot-detection signal.
                Object.defineProperty(window, 'outerWidth', {{
                    get: () => {viewport_width},
                    configurable: true
                }});
                Object.defineProperty(window, 'outerHeight', {{
                    get: () => {viewport_height} + 87,
                    configurable: true
                }});
            }} catch(e) {{}}

            // NOTE: navigator.connection mock moved to single block lower in the
            // script that uses profile-specific connection_info data. The previous
            // hardcoded block here was overwritten by the lower one anyway.

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
                    _maskAsNative(Navigator.prototype.getBattery, 'getBattery');
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
                _maskAsNative(Performance.prototype.now, 'now');
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
                    // Skip noise on hidden canvases (SmartCaptcha Picasso uses display:none)
                    try {{
                        const canvas = this.canvas;
                        if (canvas) {{
                            const style = canvas.getAttribute('style') || '';
                            if (style.indexOf('display') !== -1 && style.indexOf('none') !== -1) {{
                                return imageData;
                            }}
                            if (canvas.offsetParent === null && canvas.width <= 300 && canvas.height <= 300) {{
                                return imageData;
                            }}
                        }}
                    }} catch(e2) {{}}
                    return addProfileNoise(imageData);
                }};
                _maskAsNative(CanvasRenderingContext2D.prototype.getImageData, 'getImageData');

                // toDataURL/toBlob: do NOT modify canvas pixels before reading!
                // SmartCaptcha Picasso renders patterns on hidden canvases and hashes
                // the result. Any pixel modification corrupts the hash and triggers
                // captcha rejection. We pass through to the original implementation.
                HTMLCanvasElement.prototype.toDataURL = origToDataURL;
                HTMLCanvasElement.prototype.toBlob = origToBlob;
            }} catch(e) {{}}

            // --- WebGL vendor/renderer + comprehensive parameters override ---
            try {{
                const _wgp = {webgl_profile_json};

                // --- WebGL1 parameter overrides ---
                // CRITICAL: All numeric keys are official Khronos WebGL constants.
                // Previous version had MANY wrong values (e.g. 3413 was used for
                // SAMPLE_BUFFERS but 3413 = ALPHA_BITS) and several duplicate keys
                // (3410 appeared twice, 3414 twice, 3415 twice) that silently
                // dropped fields. This caused real GPU values from SwiftShader to
                // leak through despite the override \u2014 a strong fingerprint
                // mismatch with the declared NVIDIA/Intel/AMD GPU.
                const webgl1Overrides = {{
                    37445: _wgp.unmaskedVendor,                       // 0x9245 UNMASKED_VENDOR_WEBGL
                    37446: _wgp.unmaskedRenderer,                     // 0x9246 UNMASKED_RENDERER_WEBGL
                    7936:  _wgp.vendor || 'WebKit',                   // 0x1F00 VENDOR
                    7937:  _wgp.renderer || 'WebKit WebGL',           // 0x1F01 RENDERER
                    7938:  _wgp.version,                              // 0x1F02 VERSION
                    35724: _wgp.shadingLanguage,                      // 0x8B8C SHADING_LANGUAGE_VERSION
                    3379:  parseInt(_wgp.maxTextureSize),             // 0x0D33 MAX_TEXTURE_SIZE
                    3386:  new Int32Array(_wgp.maxViewportDims),      // 0x0D3A MAX_VIEWPORT_DIMS
                    34921: parseInt(_wgp.maxVertexAttribs),           // 0x8869 MAX_VERTEX_ATTRIBS
                    36349: parseInt(_wgp.maxFragmentUniformVectors),  // 0x8DFD MAX_FRAGMENT_UNIFORM_VECTORS
                    36347: parseInt(_wgp.maxVertexUniformVectors),    // 0x8DFB MAX_VERTEX_UNIFORM_VECTORS
                    34076: parseInt(_wgp.maxCubeMapTextureSize),      // 0x851C MAX_CUBE_MAP_TEXTURE_SIZE
                    34024: parseInt(_wgp.maxRenderBufferSize),        // 0x84E8 MAX_RENDERBUFFER_SIZE
                    35661: parseInt(_wgp.maxCombinedTextureImageUnits), // 0x8B4D MAX_COMBINED_TEXTURE_IMAGE_UNITS
                    34930: parseInt(_wgp.maxTextureImageUnits),       // 0x8872 MAX_TEXTURE_IMAGE_UNITS
                    35660: parseInt(_wgp.maxVertexTextureImageUnits), // 0x8B4C MAX_VERTEX_TEXTURE_IMAGE_UNITS
                    36348: parseInt(_wgp.maxVaryingVectors),          // 0x8DFC MAX_VARYING_VECTORS
                    32936: parseInt(_wgp.sampleBuffers),              // 0x80A8 SAMPLE_BUFFERS
                    32937: parseInt(_wgp.samples),                    // 0x80A9 SAMPLES
                    33902: new Float32Array(_wgp.aliasedLineWidthRange),  // 0x846E ALIASED_LINE_WIDTH_RANGE
                    33901: new Float32Array(_wgp.aliasedPointSizeRange),  // 0x846D ALIASED_POINT_SIZE_RANGE
                    3410:  parseInt(_wgp.redBits),                    // 0x0D52 RED_BITS
                    3411:  parseInt(_wgp.greenBits),                  // 0x0D53 GREEN_BITS
                    3412:  parseInt(_wgp.blueBits),                   // 0x0D54 BLUE_BITS
                    3413:  parseInt(_wgp.alphaBits),                  // 0x0D55 ALPHA_BITS
                    3414:  parseInt(_wgp.depthBits),                  // 0x0D56 DEPTH_BITS
                    3415:  parseInt(_wgp.stencilBits),                // 0x0D57 STENCIL_BITS
                    3408:  parseInt(_wgp.subpixelBits),               // 0x0D50 SUBPIXEL_BITS
                    36004: parseInt(_wgp.stencilBackValueMask),       // 0x8CA4 STENCIL_BACK_VALUE_MASK
                    36005: parseInt(_wgp.stencilBackWritemask),       // 0x8CA5 STENCIL_BACK_WRITEMASK
                    2963:  parseInt(_wgp.stencilValueMask),           // 0x0B93 STENCIL_VALUE_MASK
                    2968:  parseInt(_wgp.stencilWritemask),           // 0x0B98 STENCIL_WRITEMASK
                    34047: parseInt(_wgp.maxAnisotropy || '16'),      // 0x84FF MAX_TEXTURE_MAX_ANISOTROPY_EXT
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
                _maskAsNative(WebGLRenderingContext.prototype.getParameter, 'getParameter');

                // --- Patch getParameter for WebGL2 ---
                if (typeof WebGL2RenderingContext !== 'undefined') {{
                    const origGetParam2 = WebGL2RenderingContext.prototype.getParameter;
                    WebGL2RenderingContext.prototype.getParameter = function(param) {{
                        if (webgl2Overrides.hasOwnProperty(param)) {{
                            return webgl2Overrides[param];
                        }}
                        return origGetParam2.call(this, param);
                    }};
                    _maskAsNative(WebGL2RenderingContext.prototype.getParameter, 'getParameter');
                }}

                // --- Patch getSupportedExtensions ---
                const _wgl1Exts = (_wgp.extensions || '').split(',').map(s => s.trim()).filter(Boolean);
                const _wgl2Exts = (_wgp.extensions2 || '').split(',').map(s => s.trim()).filter(Boolean);
                const origGetExts1 = WebGLRenderingContext.prototype.getSupportedExtensions;
                WebGLRenderingContext.prototype.getSupportedExtensions = function() {{
                    return _wgl1Exts.length > 0 ? _wgl1Exts : origGetExts1.call(this);
                }};
                _maskAsNative(WebGLRenderingContext.prototype.getSupportedExtensions, 'getSupportedExtensions');
                if (typeof WebGL2RenderingContext !== 'undefined') {{
                    const origGetExts2 = WebGL2RenderingContext.prototype.getSupportedExtensions;
                    WebGL2RenderingContext.prototype.getSupportedExtensions = function() {{
                        return _wgl2Exts.length > 0 ? _wgl2Exts : origGetExts2.call(this);
                    }};
                    _maskAsNative(WebGL2RenderingContext.prototype.getSupportedExtensions, 'getSupportedExtensions');
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

            // --- DOMRect: pass-through (no noise) ---
            // Artificial DOMRect noise is detectable; competitor uses noise:0, doNotRound:true
            // We simply leave getBoundingClientRect/getClientRects unpatched.

            // --- AudioContext fingerprint spoofing ---
            try {{
                const origCreateOscillator = AudioContext.prototype.createOscillator || 
                                              (typeof OfflineAudioContext !== 'undefined' && OfflineAudioContext.prototype.createOscillator);
                
                // Seeded PRNG so audio noise is DETERMINISTIC per profile.
                // Real audio fingerprint is identical on every call; using
                // Math.random() each time made two consecutive calls return
                // different values — a trivial bot signal.
                const AUDIO_SEED = ({canvas_seed} ^ 0xA1D70) >>> 0;
                function _audioRng(seed) {{
                    return function() {{
                        seed |= 0; seed = seed + 0x6D2B79F5 | 0;
                        var t = Math.imul(seed ^ seed >>> 15, 1 | seed);
                        t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
                        return ((t ^ t >>> 14) >>> 0) / 4294967296;
                    }};
                }}

                // Patch AnalyserNode.getFloatFrequencyData to add stable noise
                const origGetFloat = AnalyserNode.prototype.getFloatFrequencyData;
                AnalyserNode.prototype.getFloatFrequencyData = function(array) {{
                    origGetFloat.call(this, array);
                    const rng = _audioRng(AUDIO_SEED ^ (array.length & 0xFFFF));
                    for (let i = 0; i < array.length; i += 3) {{
                        array[i] = array[i] + (rng() * 0.0001 - 0.00005);
                    }}
                }};
                _maskAsNative(AnalyserNode.prototype.getFloatFrequencyData, 'getFloatFrequencyData');

                // Patch OfflineAudioContext.startRendering
                if (typeof OfflineAudioContext !== 'undefined') {{
                    const origStartRendering = OfflineAudioContext.prototype.startRendering;
                    OfflineAudioContext.prototype.startRendering = function() {{
                        return origStartRendering.call(this).then(function(buffer) {{
                            // Stable per-profile noise on rendered audio buffer
                            const channel = buffer.getChannelData(0);
                            const rng = _audioRng(AUDIO_SEED ^ (channel.length & 0xFFFF));
                            for (let i = 0; i < channel.length; i += 100) {{
                                channel[i] = channel[i] + (rng() * 0.0000001 - 0.00000005);
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
                _maskAsNative(Permissions.prototype.query, 'query');
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
            // Clamp jsHeapSizeLimit to actual V8 --max-old-space-size+new-space.
            // Profile may declare 4GB but if Node was launched with 1GB old-space,
            // an antibot could allocate Float64Array((claimed_limit-256MB)/8) and
            // catch a RangeError before the declared limit → instant bot signal.
            // Formula: V8 reserves old_space + ~256MB for new/large-object spaces.
            try {{
                const _profileHeap = {heap_size};
                const _v8MaxOldMb = {js_heap_mb};
                const _v8RealLimit = (_v8MaxOldMb + 256) * 1024 * 1024;  // bytes
                const _heapSize = Math.min(_profileHeap, _v8RealLimit);
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
                    const _brands = {'true' if is_yabrowser else 'false'} ? [
                        {{ brand: "Chromium", version: "{chrome_version.split('.')[0] if '.' in str(chrome_version) else '145'}" }},
                        {{ brand: "YaBrowser", version: "{ya_major if ya_major else '27'}" }},
                        {{ brand: "Yowser", version: "2" }},
                        {{ brand: "Not_A Brand", version: "{ya_major if ya_major else '27'}" }}
                    ] : [
                        {{ brand: "Chromium", version: "{chrome_version.split('.')[0] if '.' in str(chrome_version) else '145'}" }},
                        {{ brand: "Google Chrome", version: "{chrome_version.split('.')[0] if '.' in str(chrome_version) else '145'}" }},
                        {{ brand: "Not-A.Brand", version: "99" }}
                    ];
                    const _fullBrands = {'true' if is_yabrowser else 'false'} ? [
                        {{ brand: "Chromium", version: "{chrome_version}" }},
                        {{ brand: "YaBrowser", version: "{ya_version if ya_version else chrome_version}" }},
                        {{ brand: "Yowser", version: "2.5" }},
                        {{ brand: "Not_A Brand", version: "{ya_version if ya_version else chrome_version}" }}
                    ] : [
                        {{ brand: "Chromium", version: "{chrome_version}" }},
                        {{ brand: "Google Chrome", version: "{chrome_version}" }},
                        {{ brand: "Not-A.Brand", version: "99.0.0.0" }}
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
                                        platformVersion: "{ua_platform_version}",
                                        architecture: "{ua_architecture}",
                                        bitness: "{ua_bitness}",
                                        model: "{ua_model}",
                                        uaFullVersion: "{'%s' % (ya_version if ya_version else chrome_version)}",
                                        fullVersionList: _fullBrands
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

            // --- mediaDevices.enumerateDevices mock ---
            // Headless Chrome returns empty array; real browsers return at least 1 audio output
            try {{
                if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {{
                    const _origEnum = navigator.mediaDevices.enumerateDevices.bind(navigator.mediaDevices);
                    const _patchedEnum = function() {{
                        return _origEnum().then(function(devices) {{
                            if (devices.length === 0) {{
                                return [
                                    {{ deviceId: "", kind: "audioinput", label: "", groupId: "" }},
                                    {{ deviceId: "", kind: "audiooutput", label: "", groupId: "" }},
                                    {{ deviceId: "", kind: "videoinput", label: "", groupId: "" }}
                                ];
                            }}
                            return devices;
                        }});
                    }};
                    _maskAsNative(_patchedEnum, 'enumerateDevices');
                    navigator.mediaDevices.enumerateDevices = _patchedEnum;
                }}
            }} catch(e) {{}}

            // --- Enhanced plugins/mimes mock ---
            // Real desktop Chrome 145+ ships exactly these 5 internal PDF plugins
            // for every user (NOT per-profile-varied — varying would be a signal).
            // Real mobile Chrome on Android has NO plugins/mimes at all (length=0).
            try {{
                const _isMobileUA = {'true' if is_mobile else 'false'};
                const pluginArr = _isMobileUA ? [] : [
                    {{ name: "PDF Viewer", filename: "internal-pdf-viewer", description: "Portable Document Format" }},
                    {{ name: "Chrome PDF Viewer", filename: "internal-pdf-viewer", description: "Portable Document Format" }},
                    {{ name: "Chromium PDF Viewer", filename: "internal-pdf-viewer", description: "Portable Document Format" }},
                    {{ name: "Microsoft Edge PDF Viewer", filename: "internal-pdf-viewer", description: "Portable Document Format" }},
                    {{ name: "WebKit built-in PDF", filename: "internal-pdf-viewer", description: "Portable Document Format" }}
                ];
                const mimeArr = _isMobileUA ? [] : [
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
                // pdfViewerEnabled: true on desktop, false on mobile Android
                Object.defineProperty(navigator, 'pdfViewerEnabled', {{
                    get: function() {{ return !_isMobileUA; }},
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
                                    get: () => false,
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

            # Inject via Playwright context.add_init_script — runs BEFORE page JS on every navigation
            # NOTE: Page.addScriptToEvaluateOnNewDocument via CDP does NOT work in persistent context
            # because the CDP session targets the page, not the browser context.
            # context.add_init_script() is the correct Playwright API for this.
            driver._context.add_init_script(stealth_script)
            logger.info("✅ Stealth fingerprint scripts injected via context.add_init_script (pre-page-load)")

        except Exception as e:
            logger.error(f"Error injecting fingerprint scripts via add_init_script: {e}")
            # Fallback: inject via execute_script (works on current page only, not new navigations)
            try:
                driver.execute_script(stealth_script)
                logger.warning("⚠️ Fingerprint scripts injected via execute_script (fallback — current page only)")
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
                logger.warning(f"Page load timeout ({timeout}s) for {url}, stopping pending navigation...")
                # CRITICAL: Use CDP Page.stopLoading BEFORE any evaluate() calls.
                # After goto() timeout, Playwright keeps navigation pending internally.
                # Any page.evaluate() will wait for navigation to settle first,
                # causing an INFINITE HANG that leads to Celery SIGKILL.
                try:
                    driver.execute_cdp_cmd("Page.stopLoading")
                except Exception:
                    pass
                # Now it's safe to check if page is usable
                try:
                    page = getattr(driver, '_page', None)
                    if page:
                        old_to = page._timeout_settings._timeout if hasattr(page, '_timeout_settings') else None
                        try:
                            page.set_default_timeout(5000)
                            state = page.evaluate("() => document.readyState")
                            current = page.url
                        finally:
                            try:
                                page.set_default_timeout(old_to if old_to is not None else 30000)
                            except Exception:
                                pass
                    else:
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
        """Close browser session and clean up Chrome processes.
        
        Strategy: Kill Chrome by PID/profile-dir FIRST, then try graceful Playwright close.
        This prevents the common scenario where context.close() hangs on a dead proxy,
        the worker gets SIGKILL'd by Celery, and Chrome processes survive as orphans.
        """
        pids = self.browser_pids.pop(browser_id, {})
        chrome_pid = pids.get('chrome_pid')
        node_driver_pid = pids.get('node_driver_pid')
        profile_dir = pids.get('profile_dir', '')
        
        logger.info(f"🔒 Closing browser {browser_id} (chrome_pid={chrome_pid}, node_driver={node_driver_pid}, dir={profile_dir})")
        
        # Step 1: Kill Chrome processes by PID FIRST (before graceful close)
        # This ensures Chrome dies even if context.close() blocks on dead proxy
        if chrome_pid:
            _kill_process_tree(chrome_pid)
        
        # Step 2: Kill ALL Chrome processes by profile directory
        # This catches Chrome children even when chrome_pid was None
        if profile_dir:
            self._kill_chrome_by_profile_dir(profile_dir)
        
        # Step 3: Try graceful close via Playwright (context.close after Chrome is already dead)
        # This is fast since Chrome is already killed — just cleans up Playwright internal state.
        # Stop any pending navigation first to prevent driver.quit() from hanging.
        # Use a thread with timeout to prevent blocking if the Playwright node driver is stuck.
        try:
            if browser_id in self.active_browsers:
                driver = self.active_browsers[browser_id]
                
                def _graceful_quit():
                    try:
                        driver.execute_cdp_cmd("Page.stopLoading")
                    except Exception:
                        pass
                    try:
                        driver.quit()
                    except Exception:
                        pass
                
                import threading
                quit_thread = threading.Thread(target=_graceful_quit, daemon=True)
                quit_thread.start()
                quit_thread.join(timeout=15)  # 15s max for graceful Playwright close
                if quit_thread.is_alive():
                    logger.warning(f"⏰ driver.quit() timed out for {browser_id} — Playwright node driver may be stuck")
                    # Kill node-driver directly if we can find it
                    if node_driver_pid:
                        _kill_process_tree(node_driver_pid)
        except Exception as e:
            logger.warning(f"Error during graceful close for {browser_id}: {e}")
        
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
        """Find and kill ALL Chrome AND node-driver processes that use a specific profile directory."""
        killed = 0
        # Resolve to absolute path — Chrome cmdline always uses absolute paths
        abs_profile_dir = os.path.abspath(profile_dir)
        try:
            import psutil
            node_driver_pids = set()
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    name = (proc.info.get('name') or '').lower()
                    if 'chrome' not in name and 'chromedriver' not in name:
                        continue
                    cmdline = ' '.join(proc.info.get('cmdline') or [])
                    if abs_profile_dir in cmdline:
                        # Track the node-driver parent of this Chrome
                        try:
                            parent = psutil.Process(proc.info['pid']).parent()
                            if parent and 'run-driver' in ' '.join(parent.cmdline()):
                                node_driver_pids.add(parent.pid)
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                        proc.kill()
                        killed += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
            # Also kill orphaned node-drivers whose Chrome we just killed
            for nd_pid in node_driver_pids:
                try:
                    _kill_process_tree(nd_pid)
                    killed += 1
                except Exception:
                    pass
            if killed:
                logger.info(f"🔪 Killed {killed} Chrome/node-driver processes for {os.path.basename(profile_dir)}")
        except ImportError:
            # psutil not available — use pkill
            try:
                subprocess.run(
                    ['pkill', '-9', '-f', abs_profile_dir],
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