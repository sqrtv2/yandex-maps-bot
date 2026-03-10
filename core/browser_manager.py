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
import socket
import select
import threading
import base64
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
import fcntl
import undetected_chromedriver as uc
from webdriver_manager.chrome import ChromeDriverManager

from app.config import settings
from .profile_generator import ProfileGenerator

logger = logging.getLogger(__name__)

# File-based lock path for cross-process synchronisation (works across Celery forks)
_CHROMEDRIVER_LOCK_PATH = os.path.join(tempfile.gettempdir(), '.chromedriver_patch.lock')

# Cache for the pre-patched chromedriver path
_patched_chromedriver_path = None
_chrome_version_main = None


def _detect_chrome_version() -> int:
    """Auto-detect installed Chrome major version."""
    global _chrome_version_main
    if _chrome_version_main:
        return _chrome_version_main
    try:
        for binary in ['/opt/google/chrome/chrome', 'google-chrome', 'google-chrome-stable', 'chromium-browser', 'chromium']:
            try:
                result = subprocess.run([binary, '--version'], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    import re as _re
                    match = _re.search(r'(\d+)\.', result.stdout)
                    if match:
                        _chrome_version_main = int(match.group(1))
                        logger.info(f"🔍 Detected Chrome version: {_chrome_version_main}")
                        return _chrome_version_main
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
    except Exception as e:
        logger.warning(f"Could not detect Chrome version: {e}")
    _chrome_version_main = 145  # fallback
    logger.warning(f"⚠️ Using fallback Chrome version: {_chrome_version_main}")
    return _chrome_version_main


def _ensure_patched_chromedriver() -> str:
    """Pre-patch chromedriver once and return the path to the patched binary.
    
    undetected_chromedriver's Patcher renames the binary during patching,
    which causes FileNotFoundError / NoSuchDriverException when multiple
    Celery fork-pool workers do it simultaneously.
    
    We use a FILE-BASED lock (fcntl.flock) that actually works across
    forked processes, unlike multiprocessing.Lock which is copied on fork.
    """
    global _patched_chromedriver_path
    if _patched_chromedriver_path and os.path.exists(_patched_chromedriver_path):
        return _patched_chromedriver_path
    
    # Acquire an exclusive file lock visible to ALL processes on this machine
    lock_fd = open(_CHROMEDRIVER_LOCK_PATH, 'w')
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        
        # Double-check after acquiring lock
        if _patched_chromedriver_path and os.path.exists(_patched_chromedriver_path):
            return _patched_chromedriver_path
        
        chrome_ver = _detect_chrome_version()
        logger.info(f"🔧 Pre-patching chromedriver for Chrome {chrome_ver} (one-time)...")
        patcher = uc.Patcher(version_main=chrome_ver)
        patcher.auto()
        _patched_chromedriver_path = patcher.executable_path
        logger.info(f"✅ Chromedriver pre-patched: {_patched_chromedriver_path}")
        return _patched_chromedriver_path
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


def _kill_process_tree(pid: int):
    """Kill a process and all its children (Chrome spawns many sub-processes)."""
    try:
        import psutil
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        # Kill children first
        for child in children:
            try:
                child.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        # Kill parent
        try:
            parent.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        # Wait for all to die
        psutil.wait_procs(children + [parent], timeout=5)
    except ImportError:
        # psutil not available — fallback to os.kill
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    except Exception as e:
        # Process already dead or other error
        try:
            os.kill(pid, signal.SIGKILL)
        except:
            pass


def cleanup_orphaned_chrome():
    """Kill ALL orphaned Chrome/chromedriver processes aggressively."""
    killed = 0
    try:
        # Fast path: use pkill to kill all chrome/chromedriver at once
        result = subprocess.run(
            ['pkill', '-9', '-f', 'chromedriver'],
            capture_output=True, timeout=5
        )
        result2 = subprocess.run(
            ['pkill', '-9', '-f', 'chrome.*--no-sandbox'],
            capture_output=True, timeout=5
        )
        # Count what we killed
        try:
            count_result = subprocess.run(
                ['sh', '-c', 'pgrep -c chrome || echo 0'],
                capture_output=True, text=True, timeout=5
            )
            remaining = int(count_result.stdout.strip())
            if remaining > 0:
                # Force kill everything Chrome-related
                subprocess.run(['pkill', '-9', 'chrome'], capture_output=True, timeout=5)
                killed = remaining
        except Exception:
            pass
        
        if killed:
            logger.info(f"🧹 Cleaned up Chrome processes ({killed} were still running)")
    except Exception as e:
        logger.warning(f"Error in cleanup_orphaned_chrome: {e}")
    return killed


class _LocalProxyForwarder:
    """Local HTTP proxy that forwards all traffic to a remote proxy with auth.

    Chrome extensions don't work with undetected_chromedriver, so we run
    a tiny local proxy (no auth) and tell Chrome to use it via --proxy-server.
    The local proxy forwards everything to the remote authenticated proxy.
    Supports both HTTP and SOCKS5 remote proxies.
    """

    def __init__(self, remote_host: str, remote_port: int, username: str, password: str,
                 proxy_type: str = 'http'):
        self.remote_host = remote_host
        self.remote_port = remote_port
        self.username = username
        self.password = password
        self.auth_header = base64.b64encode(f"{username}:{password}".encode()).decode()
        self.proxy_type = proxy_type.lower()  # 'http' or 'socks5'
        self.server = None
        self.thread = None
        self.local_port = None

    def _socks5_connect(self, target_host: str, target_port: int) -> socket.socket:
        """Establish a SOCKS5 connection to the target through the remote proxy."""
        remote = socket.create_connection(
            (self.remote_host, self.remote_port), timeout=30
        )
        # SOCKS5 greeting: version=5, 1 auth method: 02=username/password
        remote.sendall(b'\x05\x01\x02')
        resp = remote.recv(2)
        if len(resp) < 2 or resp[0] != 5:
            remote.close()
            raise Exception("SOCKS5 handshake failed: bad greeting response")
        if resp[1] == 0x02:
            # Username/password auth (RFC 1929)
            uname = self.username.encode()
            pwd = self.password.encode()
            remote.sendall(b'\x01' + bytes([len(uname)]) + uname + bytes([len(pwd)]) + pwd)
            auth_resp = remote.recv(2)
            if len(auth_resp) < 2 or auth_resp[1] != 0:
                remote.close()
                raise Exception("SOCKS5 authentication failed")
        elif resp[1] == 0xFF:
            remote.close()
            raise Exception("SOCKS5: no acceptable auth methods")

        # CONNECT request — use domain name (ATYP=0x03)
        addr_bytes = target_host.encode()
        remote.sendall(
            b'\x05\x01\x00\x03'
            + bytes([len(addr_bytes)]) + addr_bytes
            + target_port.to_bytes(2, 'big')
        )
        # Read response (at least 10 bytes for IPv4 reply)
        connect_resp = remote.recv(10)
        if len(connect_resp) < 2 or connect_resp[1] != 0:
            code = connect_resp[1] if len(connect_resp) >= 2 else -1
            remote.close()
            raise Exception(f"SOCKS5 connect failed, code={code}")
        return remote

    def start(self) -> int:
        """Start the local proxy and return its port number."""
        import http.server
        import socketserver

        forwarder = self  # Capture reference for handler

        class ProxyHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass  # Suppress request logs

            def do_CONNECT(self):
                """Handle HTTPS CONNECT tunnel through remote proxy."""
                try:
                    # Parse target host:port from CONNECT request
                    if ':' in self.path:
                        target_host, target_port = self.path.rsplit(':', 1)
                        target_port = int(target_port)
                    else:
                        target_host = self.path
                        target_port = 443

                    if forwarder.proxy_type == 'socks5':
                        remote = forwarder._socks5_connect(target_host, target_port)
                    else:
                        # HTTP proxy CONNECT
                        remote = socket.create_connection(
                            (forwarder.remote_host, forwarder.remote_port), timeout=30
                        )
                        connect_req = (
                            f"CONNECT {self.path} HTTP/1.1\r\n"
                            f"Host: {self.path}\r\n"
                            f"Proxy-Authorization: Basic {forwarder.auth_header}\r\n"
                            f"\r\n"
                        )
                        remote.sendall(connect_req.encode())

                        response = b""
                        while b"\r\n\r\n" not in response:
                            chunk = remote.recv(4096)
                            if not chunk:
                                break
                            response += chunk

                        status_line = response.split(b"\r\n")[0]
                        if b"200" not in status_line:
                            self.send_error(502, f"Remote proxy error: {status_line[:80]}")
                            remote.close()
                            return

                    self.send_response(200, "Connection Established")
                    self.end_headers()
                    self._tunnel(self.connection, remote)
                except Exception as e:
                    try:
                        self.send_error(502, str(e)[:200])
                    except Exception:
                        pass

            def _tunnel(self, client, remote):
                """Bidirectional data tunnel."""
                sockets = [client, remote]
                try:
                    while True:
                        readable, _, err = select.select(sockets, [], sockets, 300)
                        if err or not readable:
                            break
                        for s in readable:
                            data = s.recv(65536)
                            if not data:
                                return
                            if s is client:
                                remote.sendall(data)
                            else:
                                client.sendall(data)
                except Exception:
                    pass
                finally:
                    try:
                        remote.close()
                    except Exception:
                        pass

            def do_GET(self):
                self._proxy_request()

            def do_POST(self):
                self._proxy_request()

            def do_PUT(self):
                self._proxy_request()

            def do_DELETE(self):
                self._proxy_request()

            def do_HEAD(self):
                self._proxy_request()

            def do_OPTIONS(self):
                self._proxy_request()

            def _proxy_request(self):
                """Forward HTTP request through remote proxy with auth."""
                try:
                    if forwarder.proxy_type == 'socks5':
                        # For plain HTTP through SOCKS5: parse URL, connect via SOCKS5, send request directly
                        from urllib.parse import urlparse
                        parsed = urlparse(self.path)
                        target_host = parsed.hostname
                        target_port = parsed.port or 80
                        remote = forwarder._socks5_connect(target_host, target_port)
                        # Rewrite request path to be relative
                        rel_path = parsed.path or '/'
                        if parsed.query:
                            rel_path += '?' + parsed.query
                        req_line = f"{self.command} {rel_path} HTTP/1.1\r\n"
                        headers = f"Host: {target_host}\r\n"
                        for key, val in self.headers.items():
                            if key.lower() not in ('proxy-authorization', 'proxy-connection'):
                                headers += f"{key}: {val}\r\n"
                        content_length = int(self.headers.get('Content-Length', 0))
                        body = self.rfile.read(content_length) if content_length else b""
                        remote.sendall((req_line + headers + "\r\n").encode() + body)
                    else:
                        # HTTP proxy: forward with Proxy-Authorization
                        remote = socket.create_connection(
                            (forwarder.remote_host, forwarder.remote_port), timeout=30
                        )
                        req_line = f"{self.command} {self.path} HTTP/1.1\r\n"
                        headers = f"Proxy-Authorization: Basic {forwarder.auth_header}\r\n"
                        for key, val in self.headers.items():
                            if key.lower() != 'proxy-authorization':
                                headers += f"{key}: {val}\r\n"
                        content_length = int(self.headers.get('Content-Length', 0))
                        body = self.rfile.read(content_length) if content_length else b""
                        remote.sendall((req_line + headers + "\r\n").encode() + body)

                    # Stream response back
                    response = b""
                    while True:
                        chunk = remote.recv(65536)
                        if not chunk:
                            break
                        response += chunk
                        if len(response) > 10 * 1024 * 1024:
                            break
                    self.wfile.write(response)
                    remote.close()
                except Exception as e:
                    try:
                        self.send_error(502, str(e)[:200])
                    except Exception:
                        pass

        class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True
            allow_reuse_address = True

        # Bind to a random free port
        self.server = ThreadedServer(("127.0.0.1", 0), ProxyHandler)
        self.local_port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        logger.info(f"🔄 Local proxy forwarder started on 127.0.0.1:{self.local_port} → {self.remote_host}:{self.remote_port} ({self.proxy_type})")
        return self.local_port

    def stop(self):
        """Stop the local proxy server."""
        if self.server:
            try:
                self.server.shutdown()
                logger.info(f"🛑 Local proxy forwarder stopped (port {self.local_port})")
            except Exception:
                pass
            self.server = None


class BrowserManager:
    """Manages browser instances with profiles and automation."""

    def __init__(self):
        self.profile_generator = ProfileGenerator()
        self.active_browsers = {}  # browser_id -> browser_instance
        self.browser_profiles = {}  # browser_id -> profile_data
        self.browser_pids = {}  # browser_id -> {'chrome_pid': int, 'driver_pid': int}
        self.proxy_forwarders = {}  # browser_id -> _LocalProxyForwarder
        self.driver_path = None
        self._setup_driver()

    def _setup_driver(self):
        """Setup Chrome driver."""
        try:
            if settings.browser_binary_path and os.path.exists(settings.browser_binary_path):
                self.driver_path = settings.browser_binary_path
            else:
                # Use webdriver manager to download driver
                installed_path = ChromeDriverManager().install()
                # webdriver-manager may return THIRD_PARTY_NOTICES or LICENSE
                # instead of the actual chromedriver binary — fix that
                if installed_path and 'chromedriver' in os.path.basename(installed_path) and os.path.basename(installed_path) != 'chromedriver':
                    # e.g. .../THIRD_PARTY_NOTICES.chromedriver — point to actual binary
                    real_binary = os.path.join(os.path.dirname(installed_path), 'chromedriver')
                    if os.path.exists(real_binary):
                        installed_path = real_binary
                self.driver_path = installed_path
            logger.info(f"Chrome driver setup: {self.driver_path}")
        except Exception as e:
            logger.error(f"Error setting up driver: {e}")

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
        """Create a new browser session with specified profile."""
        local_proxy_forwarder = None
        try:
            browser_id = f"browser_{int(time.time())}_{random.randint(1000, 9999)}"

            # Repair profile directory (fix corrupted Preferences, stale locks, etc.)
            profile_dir = os.path.join(settings.browser_user_data_dir, profile_data["name"])
            self.repair_profile_dir(profile_dir)
            singleton_lock = os.path.join(profile_dir, "SingletonLock")
            if os.path.exists(singleton_lock) or os.path.islink(singleton_lock):
                try:
                    os.remove(singleton_lock)
                    logger.warning(f"🗑️ Removed stale SingletonLock for {profile_data['name']}")
                except OSError as e:
                    logger.warning(f"Could not remove SingletonLock: {e}")

            # Start local proxy forwarder for authenticated proxies
            local_proxy_port = None
            if proxy_data and proxy_data.get('username') and proxy_data.get('password'):
                local_proxy_forwarder = _LocalProxyForwarder(
                    remote_host=proxy_data['host'],
                    remote_port=int(proxy_data['port']),
                    username=proxy_data['username'],
                    password=proxy_data['password'],
                    proxy_type=proxy_data.get('proxy_type', 'http'),
                )
                local_proxy_port = local_proxy_forwarder.start()
                logger.info(f"🔐 Proxy auth via local forwarder (127.0.0.1:{local_proxy_port})")

            # Setup Chrome options
            chrome_options = self._create_chrome_options(profile_data, proxy_data, local_proxy_port)

            # Get pre-patched chromedriver path (patched once, reused by all workers)
            # This avoids the race condition where parallel uc.Chrome() calls
            # try to rename/patch the same binary simultaneously
            patched_driver = _ensure_patched_chromedriver()
            logger.info(f"Creating browser with pre-patched chromedriver: {patched_driver}")

            chrome_ver = _detect_chrome_version()
            try:
                if settings.debug:
                    driver = uc.Chrome(
                        options=chrome_options,
                        driver_executable_path=patched_driver,
                        user_data_dir=profile_dir,
                        service_args=["--verbose"],
                        version_main=chrome_ver
                    )
                else:
                    driver = uc.Chrome(
                        options=chrome_options,
                        driver_executable_path=patched_driver,
                        user_data_dir=profile_dir,
                        version_main=chrome_ver
                    )
            except Exception as chrome_exc:
                # Chrome failed to start — clean up orphaned Chrome/chromedriver
                # processes for this profile directory to prevent process leaks.
                logger.warning(f"Chrome launch failed, cleaning up orphans for {profile_dir}: {chrome_exc}")
                self._kill_chrome_by_profile_dir(profile_dir)
                # Also kill any chromedriver that might still be running from this attempt
                try:
                    subprocess.run(
                        ['pkill', '-9', '-f', f'chromedriver.*{os.path.basename(profile_dir)}'],
                        capture_output=True, timeout=5
                    )
                except Exception:
                    pass
                # Remove stale SingletonLock left by the crashed Chrome
                singleton_lock = os.path.join(profile_dir, "SingletonLock")
                if os.path.exists(singleton_lock) or os.path.islink(singleton_lock):
                    try:
                        os.remove(singleton_lock)
                    except OSError:
                        pass
                raise chrome_exc
            logger.info("✅ Browser created successfully")

            # Wait for Chrome to fully initialize before sending CDP commands.
            # Chrome 145+ in headless mode can report "Browser window not found"
            # if CDP commands arrive before the window target is ready.
            for _wait_attempt in range(5):
                try:
                    time.sleep(0.5)
                    _ = driver.current_url  # lightweight CDP health-check
                    break
                except Exception:
                    if _wait_attempt == 4:
                        logger.warning("⚠️ Browser still not responsive after 2.5s init wait")
                    continue

            # Apply profile settings (with one retry on transient crash)
            try:
                self._apply_profile_settings(driver, profile_data)
            except WebDriverException as ps_err:
                err_msg = str(ps_err).lower()
                if 'browser window not found' in err_msg or 'not reachable' in err_msg:
                    logger.warning("⚠️ Profile settings failed on first attempt, retrying after 1s...")
                    time.sleep(1)
                    try:
                        _ = driver.current_url
                    except Exception:
                        raise ps_err  # browser truly dead
                    self._apply_profile_settings(driver, profile_data)
                else:
                    raise

            # Verify browser is still alive after applying profile settings
            try:
                _ = driver.current_url
            except Exception as health_err:
                logger.error(f"💀 Browser died during profile setup: {health_err}")
                # Clean up the dead Chrome process
                try:
                    driver.quit()
                except Exception:
                    pass
                self._kill_chrome_by_profile_dir(profile_dir)
                raise WebDriverException(f"Browser crashed during profile setup: {health_err}")

            # Store browser instance
            self.active_browsers[browser_id] = driver
            self.browser_profiles[browser_id] = profile_data

            # Track PIDs for reliable cleanup
            pids = {'chrome_pid': None, 'driver_pid': None}
            try:
                if hasattr(driver, 'service') and hasattr(driver.service, 'process'):
                    pids['driver_pid'] = driver.service.process.pid
            except:
                pass
            try:
                if hasattr(driver, 'browser_pid'):
                    pids['chrome_pid'] = driver.browser_pid
                elif pids['driver_pid']:
                    # Find Chrome child process of chromedriver
                    import psutil
                    driver_proc = psutil.Process(pids['driver_pid'])
                    for child in driver_proc.children(recursive=False):
                        if 'chrome' in child.name().lower():
                            pids['chrome_pid'] = child.pid
                            break
            except:
                pass
            # Also store profile directory for reliable process cleanup
            pids['profile_dir'] = os.path.join(settings.browser_user_data_dir, profile_data["name"])
            self.browser_pids[browser_id] = pids

            # Store proxy forwarder for cleanup when browser closes
            if local_proxy_forwarder:
                self.proxy_forwarders[browser_id] = local_proxy_forwarder

            logger.info(f"Created browser session: {browser_id} (chrome_pid={pids['chrome_pid']}, driver_pid={pids['driver_pid']})")
            return browser_id

        except Exception as e:
            # Stop forwarder on error
            if local_proxy_forwarder:
                local_proxy_forwarder.stop()
            logger.error(f"Error creating browser session: {e}")
            raise

    def _create_chrome_options(self, profile_data: Dict, proxy_data: Optional[Dict] = None, local_proxy_port: Optional[int] = None) -> Options:
        """Create Chrome options based on profile data."""
        options = Options()

        # Basic settings
        if settings.browser_headless:
            options.add_argument("--headless=new")

        # Profile directory — logged but NOT set via --user-data-dir here.
        # Instead, user_data_dir is passed to uc.Chrome() constructor directly,
        # because undetected_chromedriver ignores --user-data-dir from options
        # and creates a temp dir if user_data_dir is not explicitly passed.
        profile_dir = os.path.join(settings.browser_user_data_dir, profile_data["name"])
        logger.info(f"📁 Using saved profile with cookies: {profile_dir}")

        # Window size
        viewport = profile_data.get("viewport", {"width": 1366, "height": 768})
        options.add_argument(f"--window-size={viewport['width']},{viewport['height']}")

        # User agent
        if "user_agent" in profile_data:
            options.add_argument(f"--user-agent={profile_data['user_agent']}")

        # Language — ВСЕГДА ru-RU для Яндекс-визитов
        lang = profile_data.get('language', 'ru-RU')
        # Нормализуем: если в языке есть q= (Accept-Language формат), берём первый
        if ',' in lang:
            lang_short = lang.split(',')[0].strip()
        else:
            lang_short = lang
        options.add_argument(f"--lang={lang_short}")
        # Accept-Language header — включаем ru как основной
        options.add_argument("--accept-lang=ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7")
        logger.info(f"🌐 Browser language: {lang_short}")

        # Timezone
        if "timezone" in profile_data:
            options.add_argument(f"--timezone={profile_data['timezone']}")

        # Proxy settings
        if local_proxy_port:
            # Authenticated proxy: use local forwarder
            options.add_argument(f"--proxy-server=http://127.0.0.1:{local_proxy_port}")
            logger.info(f"✅ Proxy via local forwarder: 127.0.0.1:{local_proxy_port}")
        elif proxy_data and not (proxy_data.get('username') and proxy_data.get('password')):
            # No-auth proxy: direct Chrome argument
            proxy_type = proxy_data.get('proxy_type', 'http')
            proxy_url = f"{proxy_type}://{proxy_data['host']}:{proxy_data['port']}"
            logger.info(f"✅ Using proxy without auth: {proxy_url}")
            options.add_argument(f"--proxy-server={proxy_url}")

        # Anti-detection flags from profile
        stealth_flags = profile_data.get("chrome_flags", [])
        for flag in stealth_flags:
            options.add_argument(flag)

        # Minimal required flags — avoid adding automation-revealing flags
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-blink-features=AutomationControlled")
        # Chrome 111+: allow chromedriver WebSocket connection
        options.add_argument("--remote-allow-origins=*")
        # Prevent Chrome from closing window unexpectedly
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--disable-background-timer-throttling")
        options.add_argument("--disable-backgrounding-occluded-windows")
        options.add_argument("--disable-renderer-backgrounding")
        options.add_argument("--disable-hang-monitor")
        # Memory optimization — prevent OOM kills in containers
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-software-rasterizer")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-component-extensions-with-background-pages")
        options.add_argument("--js-flags=--max-old-space-size=256")
        options.add_argument("--renderer-process-limit=1")
        # Additional memory savings
        options.add_argument("--disable-features=TranslateUI,BlinkGenPropertyTrees,IsolateOrigins,site-per-process")
        options.add_argument("--disable-site-isolation-trials")
        options.add_argument("--disable-ipc-flooding-protection")
        options.add_argument("--memory-pressure-off")
        options.add_argument("--disable-canvas-aa")
        options.add_argument("--disable-2d-canvas-clip-aa")
        options.add_argument("--aggressive-cache-discard")
        options.add_argument("--disable-application-cache")
        options.add_argument("--media-cache-size=1")
        options.add_argument("--disk-cache-size=1")

        # Prefs
        prefs = {
            "download.default_directory": settings.browser_download_dir,
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
            # Принудительно русский язык интерфейса и Accept-Language
            "intl.accept_languages": "ru-RU,ru,en-US,en",
        }

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
            is_mobile = profile_data.get('is_mobile', False)
            
            # Inject fingerprinting scripts first (uses CDP which is more
            # reliable than set_window_size in headless Chrome 145+)
            self._inject_fingerprint_scripts(driver, profile_data)

            # Set viewport size — wrapped in try/except because
            # set_window_size uses Browser.setWindowBounds which can fail
            # with "Browser window not found" in headless Chrome 145+.
            # The window size is already set via --window-size Chrome arg,
            # so this is a best-effort refinement.
            viewport = profile_data.get("viewport", {})
            if viewport:
                try:
                    if is_mobile:
                        driver.set_window_size(
                            viewport.get("width", 412) + 100,
                            viewport.get("height", 915) + 200
                        )
                    else:
                        driver.set_window_size(viewport.get("width", 1366), viewport.get("height", 768))
                except WebDriverException as win_err:
                    if 'browser window not found' in str(win_err).lower():
                        logger.warning(f"⚠️ set_window_size failed (headless race), using --window-size fallback")
                    else:
                        raise

            # Set timezone via CDP if available
            if hasattr(driver, 'execute_cdp_cmd'):
                try:
                    driver.execute_cdp_cmd('Emulation.setTimezoneOverride', {
                        'timezoneId': profile_data.get('timezone', 'Europe/Moscow')
                    })
                except Exception as e:
                    logger.warning(f"Could not set timezone via CDP: {e}")

                # Принудительно выставляем русскую локаль через CDP
                try:
                    driver.execute_cdp_cmd('Emulation.setLocaleOverride', {
                        'locale': 'ru-RU'
                    })
                    logger.info("🌐 Locale set to ru-RU via CDP")
                except Exception as e:
                    logger.debug(f"Could not set locale via CDP (not critical): {e}")

                # Принудительно Accept-Language через CDP Network
                try:
                    driver.execute_cdp_cmd('Network.setExtraHTTPHeaders', {
                        'headers': {
                            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7'
                        }
                    })
                    logger.info("🌐 Accept-Language header forced to ru-RU via CDP")
                except Exception as e:
                    logger.debug(f"Could not set Accept-Language via CDP: {e}")

                # === Mobile device emulation via CDP ===
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

                    # Enable touch events for mobile
                    try:
                        driver.execute_cdp_cmd('Emulation.setTouchEmulationEnabled', {
                            'enabled': True,
                            'maxTouchPoints': profile_data.get('max_touch_points', 5)
                        })
                        logger.info("📱 Touch emulation enabled")
                    except Exception as e:
                        logger.debug(f"Could not enable touch emulation: {e}")

                # Override userAgentData to match the user-agent string
                # This prevents detection via navigator.userAgentData mismatch
                try:
                    import re
                    ua_str = profile_data.get('user_agent', '')
                    chrome_match = re.search(r'Chrome/(\d+)\.(\d+)\.(\d+)\.(\d+)', ua_str)
                    if chrome_match:
                        major_ver = chrome_match.group(1)
                        full_ver = chrome_match.group(0).replace('Chrome/', '')
                        
                        if is_mobile:
                            # Mobile Android platform
                            ua_platform = 'Android'
                            mobile_device = profile_data.get('mobile_device', {})
                            platform_ver = mobile_device.get('android', '14') + '.0.0'
                            model = mobile_device.get('model', '')
                            architecture = ''
                            bitness = ''
                        else:
                            # Determine platform from UA
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
            # Fatal browser errors — browser window crashed or session died
            fatal_keywords = ['Browser window not found', 'invalid session id', 'session deleted',
                              'browser has closed', 'not reachable', 'disconnected']
            if any(kw.lower() in error_str.lower() for kw in fatal_keywords):
                logger.error(f"💀 Fatal error applying profile settings (browser crashed): {e}")
                raise  # Re-raise so create_browser_session knows browser is dead
            logger.error(f"Error applying profile settings: {e}")

    def _inject_fingerprint_scripts(self, driver: webdriver.Chrome, profile_data: Dict):
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
            webgl_vendor = webgl_data.get("vendor", "Google Inc. (NVIDIA)")
            webgl_renderer = webgl_data.get("renderer", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1060 6GB Direct3D11 vs_5_0 ps_5_0, D3D11)")

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
                    // Use configurable + enumerable to match real browser behavior
                    const descriptor = {{
                        get: () => value,
                        configurable: true,
                        enumerable: true
                    }};
                    Object.defineProperty(Navigator.prototype, prop, descriptor);
                }} catch(e) {{}}
            }}

            // --- Canvas fingerprint noise (dynamic per call) ---
            try {{
                const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
                const origToBlob = HTMLCanvasElement.prototype.toBlob;
                const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;

                // Add imperceptible noise to canvas pixel data
                function addNoise(imageData) {{
                    const data = imageData.data;
                    const seed = Math.random() * 0.02;
                    for (let i = 0; i < data.length; i += 4) {{
                        // Only modify a small fraction of pixels with tiny noise
                        if (Math.random() < 0.001) {{
                            data[i] = Math.min(255, data[i] + Math.floor(seed * 2));
                        }}
                    }}
                    return imageData;
                }}

                CanvasRenderingContext2D.prototype.getImageData = function(...args) {{
                    const imageData = origGetImageData.apply(this, args);
                    return addNoise(imageData);
                }};

                HTMLCanvasElement.prototype.toDataURL = function(...args) {{
                    // Add invisible pixel before export
                    try {{
                        const ctx = this.getContext('2d');
                        if (ctx) {{
                            const noise = Math.random() * 0.01;
                            ctx.fillStyle = `rgba(0,0,0,${{noise}})`;
                            ctx.fillRect(0, 0, 1, 1);
                        }}
                    }} catch(e) {{}}
                    return origToDataURL.apply(this, args);
                }};

                HTMLCanvasElement.prototype.toBlob = function(cb, ...args) {{
                    try {{
                        const ctx = this.getContext('2d');
                        if (ctx) {{
                            const noise = Math.random() * 0.01;
                            ctx.fillStyle = `rgba(0,0,0,${{noise}})`;
                            ctx.fillRect(0, 0, 1, 1);
                        }}
                    }} catch(e) {{}}
                    return origToBlob.call(this, cb, ...args);
                }};
            }} catch(e) {{}}

            // --- WebGL vendor/renderer override ---
            try {{
                const origGetParam = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(param) {{
                    if (param === 37445) return '{webgl_vendor}';
                    if (param === 37446) return '{webgl_renderer}';
                    return origGetParam.call(this, param);
                }};
                const origGetParam2 = WebGL2RenderingContext.prototype.getParameter;
                WebGL2RenderingContext.prototype.getParameter = function(param) {{
                    if (param === 37445) return '{webgl_vendor}';
                    if (param === 37446) return '{webgl_renderer}';
                    return origGetParam2.call(this, param);
                }};
            }} catch(e) {{}}

            // --- Plugins & MimeTypes to look like real Chrome ---
            try {{
                Object.defineProperty(navigator, 'plugins', {{
                    get: () => {{
                        const arr = [
                            {{name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format'}},
                            {{name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: ''}},
                            {{name: 'Native Client', filename: 'internal-nacl-plugin', description: ''}}
                        ];
                        arr.__proto__ = PluginArray.prototype;
                        Object.defineProperty(arr, 'length', {{value: 3}});
                        return arr;
                    }},
                    configurable: true,
                    enumerable: true
                }});
            }} catch(e) {{}}

            // --- Permissions API patch ---
            try {{
                const origQuery = Permissions.prototype.query;
                Permissions.prototype.query = function(params) {{
                    if (params && params.name === 'notifications') {{
                        return Promise.resolve({{state: 'prompt', onchange: null}});
                    }}
                    return origQuery.call(this, params);
                }};
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
        """Navigate browser to specified URL.
        
        Accepts 'interactive' readyState (DOM ready, sub-resources still loading)
        so pages behind slow proxies don't always timeout.
        """
        try:
            if browser_id not in self.active_browsers:
                raise ValueError(f"Browser session {browser_id} not found")

            driver = self.active_browsers[browser_id]
            driver.set_page_load_timeout(timeout)

            try:
                driver.get(url)
            except TimeoutException:
                # Page load timed out, but the page may still be partially usable
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

            # Wait for at least interactive state (DOM ready)
            WebDriverWait(driver, min(timeout, 15)).until(
                lambda d: d.execute_script("return document.readyState") in ("interactive", "complete")
            )

            logger.info(f"Successfully navigated {browser_id} to {url}")
            return True

        except TimeoutException:
            # readyState wait timed out — check if page is at least partially loaded
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

                    # Minimal delay between actions
                    time.sleep(random.uniform(0.2, 0.5))

                except Exception as e:
                    logger.warning(f"Error performing action {action}: {e}")
                    continue

            return True

        except Exception as e:
            logger.error(f"Error performing human actions in {browser_id}: {e}")
            return False

    def _perform_scroll(self, driver: webdriver.Chrome):
        """Human-like smooth scrolling using wheel events."""
        max_scrolls = random.randint(2, 4)

        for _ in range(max_scrolls):
            scroll_distance = random.randint(150, 500)
            # Smooth scroll in small steps like a real mouse wheel
            steps = random.randint(3, 8)
            step_size = scroll_distance // steps
            for i in range(steps):
                driver.execute_script(f"window.scrollBy({{top: {step_size}, behavior: 'smooth'}});")
                time.sleep(random.uniform(0.02, 0.08))
            # Pause between scrolls like a human reading
            time.sleep(random.uniform(0.3, 1.2))

    def _perform_mouse_movement(self, driver: webdriver.Chrome, action_chain: ActionChains):
        """Human-like mouse movement with Bezier curves to absolute positions."""
        try:
            viewport_width = driver.execute_script("return window.innerWidth")
            viewport_height = driver.execute_script("return window.innerHeight")

            # Move to body first to establish a known position
            body = driver.find_element(By.TAG_NAME, "body")
            ActionChains(driver).move_to_element(body).perform()
            time.sleep(random.uniform(0.05, 0.15))

            for _ in range(random.randint(1, 3)):
                # Target position (relative to viewport center)
                target_x = random.randint(-viewport_width // 3, viewport_width // 3)
                target_y = random.randint(-viewport_height // 3, viewport_height // 3)

                # Move in small steps (Bezier-like curve)
                steps = random.randint(5, 15)
                for step in range(steps):
                    t = (step + 1) / steps
                    # Ease-in-out curve
                    ease = t * t * (3 - 2 * t)
                    dx = int(target_x * ease / steps)
                    dy = int(target_y * ease / steps)
                    if dx != 0 or dy != 0:
                        try:
                            ActionChains(driver).move_by_offset(dx, dy).perform()
                        except Exception:
                            break
                        time.sleep(random.uniform(0.005, 0.02))

                time.sleep(random.uniform(0.1, 0.4))
        except Exception as e:
            logger.warning(f"Mouse movement error: {e}")

    def _perform_random_click(self, driver: webdriver.Chrome):
        """Click on random safe elements using real mouse events."""
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
                        # Use ActionChains for real mouse events (move + click)
                        ActionChains(driver).move_to_element(element).pause(
                            random.uniform(0.05, 0.2)
                        ).click().perform()
                        time.sleep(random.uniform(0.3, 0.8))
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
        """Close browser session and forcefully kill ALL Chrome/driver processes."""
        pids = self.browser_pids.pop(browser_id, {})
        chrome_pid = pids.get('chrome_pid')
        driver_pid = pids.get('driver_pid')
        profile_dir = pids.get('profile_dir', '')
        
        logger.info(f"🔒 Closing browser {browser_id} (chrome_pid={chrome_pid}, driver_pid={driver_pid}, dir={profile_dir})")
        
        # Step 1: Try graceful close via Selenium
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
        if driver_pid:
            _kill_process_tree(driver_pid)
        
        # Step 3: CRITICAL — find and kill ALL Chrome processes by profile directory
        # This catches orphaned processes that driver.quit() missed
        if profile_dir:
            self._kill_chrome_by_profile_dir(profile_dir)
        
        # Step 4: Remove stale SingletonLock left by killed Chrome
        if profile_dir:
            singleton_lock = os.path.join(profile_dir, "SingletonLock")
            if os.path.exists(singleton_lock) or os.path.islink(singleton_lock):
                try:
                    os.remove(singleton_lock)
                    logger.info(f"🗑️ Cleaned up SingletonLock in {os.path.basename(profile_dir)}")
                except OSError:
                    pass

        # Step 5: Stop proxy forwarder
        forwarder = self.proxy_forwarders.pop(browser_id, None)
        if forwarder:
            forwarder.stop()

        # Step 6: Cleanup dictionaries
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

        # Stop any remaining proxy forwarders
        for bid, forwarder in list(self.proxy_forwarders.items()):
            forwarder.stop()
        self.proxy_forwarders.clear()

        # Also kill any orphaned Chrome processes from previous crashed sessions
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