"""
Selenium-compatible wrapper around Playwright (sync API).

Provides drop-in replacements for:
  - selenium.webdriver.Chrome        → PlaywrightDriver
  - selenium.webdriver.remote.webelement.WebElement → PlaywrightElement
  - selenium.webdriver.common.action_chains.ActionChains → PlaywrightActionChains
  - selenium.webdriver.support.ui.WebDriverWait → PlaywrightWait
  - selenium.webdriver.common.by.By  → By (re-exported)
  - selenium.webdriver.common.keys.Keys → Keys (re-exported)
  - selenium.common.exceptions.*     → re-exported exception classes

Allows existing task code (~8,600 lines) to work with Playwright
with ONLY import changes.
"""
from __future__ import annotations

import os
import time
import random
import logging
import re as _re
from typing import Any, Callable, Dict, List, Optional, Union

from playwright.sync_api import (
    sync_playwright,
    Browser,
    BrowserContext,
    Page,
    ElementHandle,
    Playwright,
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# Re-export Selenium-compatible constants
# ──────────────────────────────────────────────────────────────────

class By:
    """Selenium By-compatible locator strategies."""
    ID = "id"
    NAME = "name"
    CSS_SELECTOR = "css"
    TAG_NAME = "tag"
    XPATH = "xpath"
    CLASS_NAME = "class"
    LINK_TEXT = "link_text"
    PARTIAL_LINK_TEXT = "partial_link_text"


class Keys:
    """Selenium Keys-compatible key constants."""
    RETURN = "Enter"
    ENTER = "Enter"
    ESCAPE = "Escape"
    TAB = "Tab"
    BACKSPACE = "Backspace"
    DELETE = "Delete"
    SPACE = " "
    CONTROL = "Control"
    SHIFT = "Shift"
    ALT = "Alt"
    META = "Meta"
    ARROW_DOWN = "ArrowDown"
    ARROW_UP = "ArrowUp"
    ARROW_LEFT = "ArrowLeft"
    ARROW_RIGHT = "ArrowRight"
    HOME = "Home"
    END = "End"
    PAGE_UP = "PageUp"
    PAGE_DOWN = "PageDown"


# ──────────────────────────────────────────────────────────────────
# Selenium-compatible exceptions (thin wrappers)
# ──────────────────────────────────────────────────────────────────

class WebDriverException(Exception):
    pass

class TimeoutException(Exception):
    pass

class NoSuchElementException(Exception):
    pass

class ElementClickInterceptedException(Exception):
    pass

class StaleElementReferenceException(Exception):
    pass


# ──────────────────────────────────────────────────────────────────
# PlaywrightElement — wraps ElementHandle with Selenium WebElement API
# ──────────────────────────────────────────────────────────────────

class PlaywrightElement:
    """Selenium WebElement-compatible wrapper around Playwright ElementHandle."""

    def __init__(self, handle: ElementHandle, page: Page):
        self._handle = handle
        self._page = page

    # ── core actions ──

    def click(self, timeout: float = 5000):
        try:
            self._handle.click(timeout=timeout)
        except PlaywrightError as e:
            if 'intercept' in str(e).lower():
                raise ElementClickInterceptedException(str(e))
            raise WebDriverException(str(e))

    def send_keys(self, *values):
        """Type text or press special keys.

        Supports ``Keys.CONTROL + "a"`` style combos via string concatenation
        (Selenium encodes modifier combos as single strings with special chars).
        """
        for value in values:
            if value in (Keys.RETURN, Keys.ENTER, Keys.ESCAPE, Keys.TAB,
                         Keys.BACKSPACE, Keys.DELETE, Keys.ARROW_DOWN,
                         Keys.ARROW_UP, Keys.ARROW_LEFT, Keys.ARROW_RIGHT,
                         Keys.HOME, Keys.END, Keys.PAGE_UP, Keys.PAGE_DOWN):
                self._page.keyboard.press(value)
            elif value == Keys.CONTROL + "a":
                self._page.keyboard.press("Control+a")
            elif value == Keys.CONTROL + "c":
                self._page.keyboard.press("Control+c")
            elif value == Keys.CONTROL + "v":
                self._page.keyboard.press("Control+v")
            elif len(value) == 1:
                # Single character — type with small delay for realism
                self._handle.type(value, delay=random.randint(30, 80))
            else:
                # Multi-char string — type it
                self._handle.type(value, delay=random.randint(30, 80))

    def clear(self):
        try:
            self._handle.fill("")
        except Exception:
            # fallback: select all and delete
            self._handle.click()
            self._page.keyboard.press("Control+a")
            self._page.keyboard.press("Delete")

    # ── properties ──

    @property
    def text(self) -> str:
        try:
            return self._handle.text_content() or ""
        except Exception:
            return ""

    @property
    def tag_name(self) -> str:
        try:
            return self._handle.evaluate("el => el.tagName.toLowerCase()")
        except Exception:
            return ""

    @property
    def size(self) -> Dict[str, float]:
        box = self._handle.bounding_box()
        if box:
            return {"width": box["width"], "height": box["height"]}
        return {"width": 0, "height": 0}

    @property
    def location(self) -> Dict[str, float]:
        box = self._handle.bounding_box()
        if box:
            return {"x": box["x"], "y": box["y"]}
        return {"x": 0, "y": 0}

    # ── attribute / state ──

    def get_attribute(self, name: str) -> Optional[str]:
        try:
            return self._handle.get_attribute(name)
        except Exception:
            return None

    def is_displayed(self) -> bool:
        try:
            return self._handle.is_visible()
        except Exception:
            return False

    def is_enabled(self) -> bool:
        try:
            return self._handle.is_enabled()
        except Exception:
            return False

    # ── child search ──

    def find_element(self, by: str, value: str) -> 'PlaywrightElement':
        selector = _to_playwright_selector(by, value)
        try:
            handle = self._page.query_selector(selector)
        except Exception as e:
            if 'closed' in str(e).lower() or 'timeout' in str(e).lower():
                raise NoSuchElementException(f"Page closed or timeout: {by}={value}")
            raise
        if not handle:
            raise NoSuchElementException(f"No element found: {by}={value}")
        return PlaywrightElement(handle, self._page)

    def find_elements(self, by: str, value: str) -> List[PlaywrightElement]:
        selector = _to_playwright_selector(by, value)
        try:
            handles = self._page.query_selector_all(selector)
        except Exception as e:
            if 'closed' in str(e).lower() or 'timeout' in str(e).lower():
                return []
            raise
        return [PlaywrightElement(h, self._page) for h in handles]

    # ── internal ──

    @property
    def screenshot_as_png(self) -> bytes:
        """Return PNG screenshot of this element as bytes (Selenium compat)."""
        return self._handle.screenshot()

    @property
    def _element_handle(self) -> ElementHandle:
        return self._handle

    def __repr__(self):
        try:
            tag = self.tag_name
            return f"<PlaywrightElement tag={tag}>"
        except Exception:
            return "<PlaywrightElement>"


# ──────────────────────────────────────────────────────────────────
# Helper: convert By.XXX + value → Playwright selector
# ──────────────────────────────────────────────────────────────────

def _to_playwright_selector(by: str, value: str) -> str:
    if by == By.CSS_SELECTOR or by == "css":
        return value
    if by == By.TAG_NAME or by == "tag":
        return value
    if by == By.ID or by == "id":
        return f"#{value}"
    if by == By.NAME or by == "name":
        return f"[name='{value}']"
    if by == By.CLASS_NAME or by == "class":
        return f".{value}"
    if by == By.XPATH or by == "xpath":
        return f"xpath={value}"
    if by == By.LINK_TEXT or by == "link_text":
        return f"a:text-is('{value}')"
    if by == By.PARTIAL_LINK_TEXT or by == "partial_link_text":
        return f"a:text('{value}')"
    # fallback — assume CSS
    return value


# ──────────────────────────────────────────────────────────────────
# _SwitchTo — Selenium switch_to compatibility helper
# ──────────────────────────────────────────────────────────────────

class _SwitchTo:
    """Mimics Selenium's driver.switch_to interface for tab switching."""

    def __init__(self, driver: 'PlaywrightDriver'):
        self._driver = driver

    def window(self, handle: str):
        """Switch to the page (tab) identified by *handle*.

        *handle* is one of the strings returned by driver.window_handles
        (Page._guid or a stringified index).
        """
        for i, page in enumerate(self._driver._context.pages):
            page_handle = getattr(page, '_guid', str(i))
            if page_handle == handle:
                self._driver._page = page
                # Refresh CDP session for the new page
                self._driver._cdp_session = None
                page.bring_to_front()
                return
        raise NoSuchElementException(f"No window with handle {handle}")

    def frame(self, frame_ref):
        """Switch to an iframe (by index, name, or element)."""
        if isinstance(frame_ref, int):
            frames = self._driver._page.frames
            if frame_ref < len(frames):
                # Playwright doesn't truly "switch" to a frame;
                # we set the page to operate on that frame via a helper.
                pass
        # For most of our code this is a no-op; add real impl if needed.

    def default_content(self):
        """Switch back to main frame (no-op for most Playwright usage)."""
        pass


# ──────────────────────────────────────────────────────────────────
# PlaywrightDriver — wraps Page with Selenium WebDriver API
# ──────────────────────────────────────────────────────────────────

class PlaywrightDriver:
    """Selenium WebDriver-compatible wrapper around a Playwright Page.

    Stores the Playwright objects and exposes the Selenium API used
    by the task code: driver.get(), driver.find_element(), etc.
    """

    def __init__(self, page: Page, context: BrowserContext, browser: Browser,
                 playwright_instance: Playwright, browser_name: str = 'chromium'):
        self._page = page
        self._context = context
        self._browser = browser
        self._playwright = playwright_instance
        self._browser_name = browser_name
        self._page_load_timeout = 60_000  # ms
        self._script_timeout = 15_000  # ms
        self._cdp_session = None
        self._switch_to = _SwitchTo(self)
        # Set default timeout for all Playwright operations (evaluate, wait_for, etc.)
        # Prevents hanging on dead renderers / pending navigations.
        # 5s is enough for any legitimate DOM/JS operation; anything longer
        # means the page is stuck (navigation pending, renderer dead, etc.).
        self._default_timeout_ms = 5_000
        self._page.set_default_timeout(self._default_timeout_ms)

    def recover_page(self) -> bool:
        """Create a new page in the same context when the current one is dead.

        Returns True if a new page was created, False on failure.
        """
        try:
            # Check if current page is still alive using an actual call
            # (page.url is a cached property that works even on dead pages)
            try:
                self._page.evaluate("1")
                return True  # page is fine
            except Exception:
                pass
            # Create new page in existing context
            new_page = self._context.new_page()
            self._page = new_page
            self._cdp_session = None
            return True
        except Exception:
            return False

    # ── tab / window management (Selenium compat) ──

    @property
    def window_handles(self) -> list:
        """Return a list of handles for every open page in the context.

        Handles are string ids that can be passed to switch_to.window().
        We use each Page object's internal guid as a stable handle, falling
        back to the index in context.pages.
        """
        return [getattr(p, '_guid', str(i)) for i, p in enumerate(self._context.pages)]

    @property
    def current_window_handle(self) -> str:
        return getattr(self._page, '_guid', '0')

    @property
    def switch_to(self) -> '_SwitchTo':
        return self._switch_to

    # ── navigation ──

    def get(self, url: str):
        try:
            self._page.goto(url, timeout=self._page_load_timeout,
                            wait_until="commit")
        except PlaywrightTimeoutError:
            raise TimeoutException(f"Timeout navigating to {url}")
        except PlaywrightError as e:
            raise WebDriverException(str(e))
        # After commit, wait for DOM to become at least interactive
        try:
            self._page.wait_for_load_state("domcontentloaded",
                                           timeout=self._page_load_timeout)
        except PlaywrightTimeoutError:
            pass  # page is usable after commit even if DOM is slow
        except PlaywrightError:
            pass  # CSP or other errors — page is still usable after commit

    def back(self):
        try:
            self._page.go_back(timeout=self._page_load_timeout,
                               wait_until="commit")
        except PlaywrightTimeoutError:
            pass

    def forward(self):
        self._page.go_forward(timeout=self._page_load_timeout,
                              wait_until="commit")

    def refresh(self):
        try:
            self._page.reload(timeout=self._page_load_timeout,
                              wait_until="commit")
        except PlaywrightTimeoutError:
            pass

    # ── page info ──

    @property
    def current_url(self) -> str:
        return self._page.url

    @property
    def title(self) -> str:
        try:
            return self._page.title()
        except PlaywrightTimeoutError:
            raise TimeoutException('Timed out getting page title')
        except PlaywrightError as e:
            if 'timeout' in str(e).lower():
                raise TimeoutException(f'Timed out getting page title: {e}')
            raise WebDriverException(str(e))

    @property
    def page_source(self) -> str:
        try:
            return self._page.content()
        except PlaywrightTimeoutError:
            raise TimeoutException('Timed out getting page source')
        except PlaywrightError as e:
            if 'timeout' in str(e).lower():
                raise TimeoutException(f'Timed out getting page source: {e}')
            raise WebDriverException(str(e))

    # ── element lookup ──

    def find_element(self, by: str, value: str) -> PlaywrightElement:
        selector = _to_playwright_selector(by, value)
        try:
            handle = self._page.query_selector(selector)
        except Exception as e:
            if 'closed' in str(e).lower() or 'timeout' in str(e).lower():
                raise NoSuchElementException(f"Page closed or timeout: {by}={value}")
            raise
        if not handle:
            raise NoSuchElementException(f"No element found: {by}={value}")
        return PlaywrightElement(handle, self._page)

    def find_elements(self, by: str, value: str) -> List[PlaywrightElement]:
        selector = _to_playwright_selector(by, value)
        try:
            handles = self._page.query_selector_all(selector)
        except Exception as e:
            if 'closed' in str(e).lower() or 'timeout' in str(e).lower():
                return []
            raise
        return [PlaywrightElement(h, self._page) for h in handles]

    # ── JavaScript ──

    def execute_script(self, script: str, *args) -> Any:
        """Execute JavaScript in the page context.

        Selenium passes WebElement args and uses ``arguments[N]`` in JS.
        We unwrap PlaywrightElements to ElementHandles for evaluate.
        Uses a 10s timeout to prevent hanging on broken CDP connections.
        """
        # Default timeout is already 5s (set in __init__), no need to
        # change it per-call.  Just execute and handle hangs.
        try:
            return self._execute_script_inner(script, *args)
        except Exception as e:
            # If evaluate timed out, a pending navigation may be blocking it.
            # Clear it so subsequent evaluate() calls don't hang too.
            err_str = str(e).lower()
            if 'timeout' in err_str or 'navigation' in err_str:
                try:
                    self.execute_cdp_cmd("Page.stopLoading")
                except Exception:
                    pass
            raise

    def _execute_script_inner(self, script: str, *args) -> Any:
        """Inner implementation of execute_script."""
        pw_args = []
        for arg in args:
            if isinstance(arg, PlaywrightElement):
                pw_args.append(arg._handle)
            else:
                pw_args.append(arg)

        # Selenium-style scripts use `arguments[0]`, `arguments[1]`, etc.
        # Playwright's page.evaluate() can't return DOM elements (serializes to null).
        # We use evaluate_handle() + unwrap so JS returning DOM nodes works correctly.
        def _unwrap_handle(handle):
            """Convert JSHandle to Python value or PlaywrightElement."""
            try:
                element = handle.as_element()
            except Exception:
                element = None
            if element is not None:
                return PlaywrightElement(element, self._page)
            if handle.__class__.__name__ == "ElementHandle":
                return PlaywrightElement(handle, self._page)
            try:
                return handle.json_value()
            except Exception:
                # Non-serializable, non-element handle (e.g. window)
                return None

        if pw_args:
            adapted = _strip_return(script)
            if len(pw_args) == 1:
                adapted = adapted.replace('arguments[0]', '__el__')
                wrapped = f"(__el__) => {{ {adapted} }}"
                if hasattr(pw_args[0], "evaluate"):
                    # ElementHandle arg: use element_handle.evaluate so it unwraps to DOM node
                    return pw_args[0].evaluate(wrapped)
                handle = self._page.evaluate_handle(wrapped, pw_args[0])
                return _unwrap_handle(handle)
            else:
                for i in range(len(pw_args)):
                    adapted = adapted.replace(f'arguments[{i}]', f'__args__[{i}]')
                wrapped = f"(__args__) => {{ {adapted} }}"
                handle = self._page.evaluate_handle(wrapped, pw_args)
                return _unwrap_handle(handle)
        else:
            # No args — simple evaluate_handle to support DOM element returns
            try:
                handle = self._page.evaluate_handle(script)
                return _unwrap_handle(handle)
            except Exception:
                wrapped = f"() => {{ {_strip_return(script)} }}"
                handle = self._page.evaluate_handle(wrapped)
                return _unwrap_handle(handle)

    def execute_cdp_cmd(self, cmd: str, params: dict = None) -> Any:
        """Execute Chrome DevTools Protocol command.

        Playwright provides CDP access via context.new_cdp_session() for Chromium.
        """
        if self._browser_name != 'chromium':
            params = params or {}
            if cmd == "Page.stopLoading":
                try:
                    return self._page.evaluate("() => { try { window.stop(); } catch (e) {} return true; }")
                except Exception:
                    return None
            if cmd == "Network.setExtraHTTPHeaders":
                headers = params.get("headers") or {}
                if headers:
                    try:
                        self._context.set_extra_http_headers(headers)
                    except Exception:
                        pass
                return None
            logger.debug("CDP command %s ignored for browser=%s", cmd, self._browser_name)
            return None
        if self._cdp_session is None:
            self._cdp_session = self._context.new_cdp_session(self._page)
        return self._cdp_session.send(cmd, params or {})

    # ── window management ──

    def set_window_size(self, width: int, height: int):
        self._page.set_viewport_size({"width": width, "height": height})

    def get_window_size(self) -> Dict[str, int]:
        vp = self._page.viewport_size
        if vp:
            return {"width": vp["width"], "height": vp["height"]}
        return {"width": 1366, "height": 768}

    # ── timeouts ──

    def set_page_load_timeout(self, seconds: int):
        self._page_load_timeout = seconds * 1000

    def set_script_timeout(self, seconds: int):
        self._script_timeout = seconds * 1000

    # ── screenshot ──

    def save_screenshot(self, path: str) -> bool:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            self._page.screenshot(path=path, timeout=10000)
            return True
        except Exception as e:
            logger.warning(f"Screenshot failed: {e}")
            return False

    # ── cookies ──

    def get_cookies(self) -> list:
        """Return cookies in Selenium-compatible format."""
        return self._context.cookies()

    def add_cookie(self, cookie: dict):
        self._context.add_cookies([cookie])

    def delete_all_cookies(self):
        self._context.clear_cookies()

    # ── capabilities (stub for compat) ──

    @property
    def capabilities(self) -> dict:
        return {'browserName': self._browser_name, 'proxy': {}}

    # ── lifecycle ──

    def quit(self):
        """Close browser and clean up."""
        try:
            self._page.close()
        except Exception:
            pass
        try:
            self._context.close()
        except Exception:
            pass
        try:
            self._browser.close()
        except Exception:
            pass

    def close(self):
        """Close current page."""
        try:
            self._page.close()
        except Exception:
            pass

    # ── Playwright-native access (for code that needs it) ──

    @property
    def pw_page(self) -> Page:
        """Direct access to the underlying Playwright Page."""
        return self._page

    @property
    def pw_context(self) -> BrowserContext:
        return self._context

    @property
    def pw_browser(self) -> Browser:
        return self._browser

    @property
    def browser_pid(self) -> Optional[int]:
        """Chrome main process PID for cleanup tracking.
        
        With launch_persistent_context, context.browser is None so we can't use
        browser.process.pid. Instead we get the Playwright node-driver PID
        (parent of Chrome) and find the main Chrome child.
        """
        # Method 1: browser.process.pid (works with browser.launch, not persistent_context)
        try:
            if hasattr(self._browser, 'process') and self._browser.process:
                return self._browser.process.pid
        except Exception:
            pass
        # Method 2: find Chrome PID via node-driver process tree
        try:
            transport = self._context._impl_obj._channel._connection._transport
            if hasattr(transport, '_proc') and transport._proc:
                import psutil
                node_pid = transport._proc.pid
                node = psutil.Process(node_pid)
                for child in node.children(recursive=False):
                    if 'chrome' in (child.name() or '').lower():
                        return child.pid
        except Exception:
            pass
        return None

    @property
    def node_driver_pid(self) -> Optional[int]:
        """Playwright node-driver process PID — parent of Chrome process tree."""
        try:
            transport = self._context._impl_obj._channel._connection._transport
            if hasattr(transport, '_proc') and transport._proc:
                return transport._proc.pid
        except Exception:
            pass
        return None

    def __repr__(self):
        try:
            return f"<PlaywrightDriver url={self.current_url[:60]}>"
        except Exception:
            return "<PlaywrightDriver>"


# ──────────────────────────────────────────────────────────────────
# Helper: strip "return" from Selenium-style JS for Playwright
# ──────────────────────────────────────────────────────────────────

def _strip_return(script: str) -> str:
    """Ensure script uses `return` properly for Playwright evaluate.

    Selenium's execute_script implicitly wraps in function body,
    so scripts often start with `return ...`. Playwright's evaluate
    needs an expression or arrow function that returns.
    """
    s = script.strip()
    if s.startswith("return "):
        return s  # keep return, will be wrapped in function
    return s


# ──────────────────────────────────────────────────────────────────
# PlaywrightActionChains — Selenium ActionChains-compatible wrapper
# ──────────────────────────────────────────────────────────────────

class PlaywrightActionChains:
    """Drop-in replacement for selenium.webdriver.common.action_chains.ActionChains.

    Records actions and executes them on .perform().
    """

    def __init__(self, driver: PlaywrightDriver):
        self._driver = driver
        self._page = driver._page
        self._actions = []  # list of (method_name, args, kwargs)

    def move_to_element(self, element: PlaywrightElement) -> 'PlaywrightActionChains':
        self._actions.append(('move_to_element', (element,), {}))
        return self

    def move_to_element_with_offset(self, element: PlaywrightElement,
                                     xoffset: int, yoffset: int) -> 'PlaywrightActionChains':
        self._actions.append(('move_to_element_with_offset', (element, xoffset, yoffset), {}))
        return self

    def move_by_offset(self, xoffset: int, yoffset: int) -> 'PlaywrightActionChains':
        self._actions.append(('move_by_offset', (xoffset, yoffset), {}))
        return self

    def click(self, element: PlaywrightElement = None) -> 'PlaywrightActionChains':
        self._actions.append(('click', (element,), {}))
        return self

    def click_and_hold(self, element: PlaywrightElement = None) -> 'PlaywrightActionChains':
        self._actions.append(('click_and_hold', (element,), {}))
        return self

    def release(self, element: PlaywrightElement = None) -> 'PlaywrightActionChains':
        self._actions.append(('release', (element,), {}))
        return self

    def double_click(self, element: PlaywrightElement = None) -> 'PlaywrightActionChains':
        self._actions.append(('double_click', (element,), {}))
        return self

    def pause(self, seconds: float) -> 'PlaywrightActionChains':
        self._actions.append(('pause', (seconds,), {}))
        return self

    def send_keys(self, *keys) -> 'PlaywrightActionChains':
        self._actions.append(('send_keys', keys, {}))
        return self

    def scroll_by_amount(self, dx: int, dy: int) -> 'PlaywrightActionChains':
        self._actions.append(('scroll_by_amount', (dx, dy), {}))
        return self

    def perform(self):
        """Execute all queued actions with a 10s timeout to prevent hanging."""
        _ACTION_TIMEOUT_MS = 10000
        old_timeout = self._page._timeout_settings._timeout if hasattr(self._page, '_timeout_settings') else None
        try:
            self._page.set_default_timeout(_ACTION_TIMEOUT_MS)
        except Exception:
            pass

        try:
            self._perform_inner()
        finally:
            try:
                self._page.set_default_timeout(
                    old_timeout if old_timeout is not None
                    else self._driver._default_timeout_ms
                )
            except Exception:
                pass
        self._actions = []

    def _perform_inner(self):
        """Inner perform implementation."""
        mouse = self._page.mouse
        keyboard = self._page.keyboard
        _current_x = 0
        _current_y = 0

        for action_name, args, kwargs in self._actions:
            try:
                if action_name == 'move_to_element':
                    el = args[0]
                    box = el._element_handle.bounding_box()
                    if box:
                        _current_x = box['x'] + box['width'] / 2
                        _current_y = box['y'] + box['height'] / 2
                        mouse.move(_current_x, _current_y)

                elif action_name == 'move_to_element_with_offset':
                    el, xoff, yoff = args
                    box = el._element_handle.bounding_box()
                    if box:
                        # Selenium offsets from element CENTER
                        _current_x = box['x'] + box['width'] / 2 + xoff
                        _current_y = box['y'] + box['height'] / 2 + yoff
                        mouse.move(_current_x, _current_y)

                elif action_name == 'move_by_offset':
                    xoff, yoff = args
                    _current_x += xoff
                    _current_y += yoff
                    mouse.move(_current_x, _current_y)

                elif action_name == 'click':
                    el = args[0] if args and args[0] else None
                    if el:
                        box = el._element_handle.bounding_box()
                        if box:
                            _current_x = box['x'] + box['width'] / 2
                            _current_y = box['y'] + box['height'] / 2
                    mouse.click(_current_x, _current_y)

                elif action_name == 'click_and_hold':
                    el = args[0] if args and args[0] else None
                    if el:
                        box = el._element_handle.bounding_box()
                        if box:
                            _current_x = box['x'] + box['width'] / 2
                            _current_y = box['y'] + box['height'] / 2
                    mouse.move(_current_x, _current_y)
                    mouse.down()

                elif action_name == 'release':
                    mouse.up()

                elif action_name == 'double_click':
                    el = args[0] if args and args[0] else None
                    if el:
                        box = el._element_handle.bounding_box()
                        if box:
                            _current_x = box['x'] + box['width'] / 2
                            _current_y = box['y'] + box['height'] / 2
                    mouse.dblclick(_current_x, _current_y)

                elif action_name == 'pause':
                    time.sleep(args[0])

                elif action_name == 'send_keys':
                    for key in args:
                        if key in (Keys.RETURN, Keys.ENTER, Keys.ESCAPE, Keys.TAB,
                                   Keys.BACKSPACE, Keys.DELETE):
                            keyboard.press(key)
                        elif len(key) == 1:
                            keyboard.type(key)
                        else:
                            keyboard.type(key)

                elif action_name == 'scroll_by_amount':
                    dx, dy = args
                    mouse.wheel(dx, dy)

            except Exception as e:
                err_str = str(e).lower()
                if 'target page' in err_str or 'browser has been closed' in err_str or 'target closed' in err_str:
                    self._actions.clear()
                    raise  # Don't swallow browser death
                logger.warning(f"ActionChain action '{action_name}' failed: {e}")

    def reset_actions(self):
        self._actions.clear()


# ──────────────────────────────────────────────────────────────────
# PlaywrightWait — Selenium WebDriverWait-compatible wrapper
# ──────────────────────────────────────────────────────────────────

class PlaywrightWait:
    """Drop-in replacement for selenium.webdriver.support.ui.WebDriverWait."""

    def __init__(self, driver: PlaywrightDriver, timeout: float = 10, poll_frequency: float = 0.5):
        self._driver = driver
        self._timeout = timeout
        self._poll = poll_frequency

    def until(self, condition: Callable, message: str = ""):
        end_time = time.time() + self._timeout
        last_exc = None
        while time.time() < end_time:
            try:
                result = condition(self._driver)
                if result:
                    return result
            except Exception as e:
                last_exc = e
            time.sleep(self._poll)
        raise TimeoutException(message or f"Wait timed out after {self._timeout}s. Last error: {last_exc}")

    def until_not(self, condition: Callable, message: str = ""):
        end_time = time.time() + self._timeout
        while time.time() < end_time:
            try:
                result = condition(self._driver)
                if not result:
                    return True
            except Exception:
                return True
            time.sleep(self._poll)
        raise TimeoutException(message or f"Wait (until_not) timed out after {self._timeout}s")


# ──────────────────────────────────────────────────────────────────
# Expected Conditions — Selenium EC-compatible module
# ──────────────────────────────────────────────────────────────────

class expected_conditions:
    """Namespace for Selenium-compatible expected conditions."""

    @staticmethod
    def presence_of_element_located(locator: tuple):
        by, value = locator
        def _check(driver):
            try:
                return driver.find_element(by, value)
            except NoSuchElementException:
                return False
        return _check

    @staticmethod
    def visibility_of_element_located(locator: tuple):
        by, value = locator
        def _check(driver):
            try:
                el = driver.find_element(by, value)
                if el.is_displayed():
                    return el
                return False
            except NoSuchElementException:
                return False
        return _check

    @staticmethod
    def element_to_be_clickable(locator: tuple):
        by, value = locator
        def _check(driver):
            try:
                el = driver.find_element(by, value)
                if el.is_displayed() and el.is_enabled():
                    return el
                return False
            except NoSuchElementException:
                return False
        return _check

    @staticmethod
    def presence_of_all_elements_located(locator: tuple):
        by, value = locator
        def _check(driver):
            elems = driver.find_elements(by, value)
            return elems if elems else False
        return _check

    @staticmethod
    def title_contains(text: str):
        def _check(driver):
            return text in driver.title
        return _check

    @staticmethod
    def url_contains(text: str):
        def _check(driver):
            return text in driver.current_url
        return _check


# Alias for import compatibility
EC = expected_conditions
