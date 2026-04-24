"""Visual diagnostic test: open ya.ru with REAL production browser stack
(profile + proxy + stealth) and try to find the search input.

Does NOT click, NOT type, NOT search. Just reports what it sees.

Usage (inside celery_yandex_search container):
    docker exec yandex-maps-bot-celery_yandex_search-1 \
        python /app/_test_open_yaru.py [profile_id]
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime

LOG_FMT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FMT, stream=sys.stdout, force=True)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("rebrowser_playwright").setLevel(logging.WARNING)
log = logging.getLogger("open_yaru")


def banner(msg: str) -> None:
    log.info("─" * 74)
    log.info(f"▶ {msg}")
    log.info("─" * 74)


# Same selector list production uses (tasks/yandex_search.py)
INPUT_SELECTORS = [
    "input.search3__input", "input.mini-suggest__input",
    "input.HeaderDesktopForm-Input", "input.input__control",
    "input.HeaderPhone-Input", "input.HeaderMobileForm-Input",
    "input.search-input__input", "input.input__control[name='text']",
    ".search-arrow input",
    "input#text", "input[name='text']", "textarea[name='text']",
    "input[aria-label*='Запрос']", "input[aria-label*='запрос']",
    "input[aria-label*='Поиск']", "input[aria-label*='поиск']",
    "input[aria-label*='Search']",
    "input[role='searchbox']", "input[role='combobox']",
    "#search-input input", ".search2__input input", ".search3 input",
    "[class*='search'] input[type='text']", "[class*='Search'] input[type='text']",
    "form input[type='text']", "form input[type='search']",
    "form input:not([type='hidden'])",
    # textareas (modern ya.ru uses textarea)
    "textarea#text", "textarea.search3__input", "textarea.mini-suggest__input",
]


def main() -> int:
    profile_id_arg = int(sys.argv[1]) if len(sys.argv) > 1 else 0

    banner("STEP 0: pick profile + proxy from DB")
    from app.database import SessionLocal
    from app.models import BrowserProfile
    from core.browser_manager import BrowserManager
    from core.profile_generator import ProfileGenerator
    from core.proxy_manager import ProxyManager

    db = SessionLocal()
    try:
        q = db.query(BrowserProfile).filter(
            BrowserProfile.is_active == True,
            BrowserProfile.warmup_completed == True,
        )
        if profile_id_arg:
            profile_obj = q.filter(BrowserProfile.id == profile_id_arg).first()
        else:
            profile_obj = q.order_by(BrowserProfile.last_used_at.desc().nullslast()).first()
        if not profile_obj:
            log.error("No warmed profile in DB")
            return 2

        log.info(f"Profile: id={profile_obj.id} name={profile_obj.name} "
                 f"mobile={profile_obj.is_mobile}")

        # snapshot fields we need before session closes
        profile_data_from_db = {
            'name': profile_obj.name,
            'user_agent': profile_obj.user_agent,
            'viewport_width': profile_obj.viewport_width,
            'viewport_height': profile_obj.viewport_height,
            'timezone': profile_obj.timezone,
            'language': profile_obj.language,
            'platform': profile_obj.platform,
            'is_mobile': profile_obj.is_mobile,
            'canvas_fingerprint': profile_obj.canvas_fingerprint,
            'webgl_fingerprint': profile_obj.webgl_fingerprint,
            'audio_fingerprint': profile_obj.audio_fingerprint,
            'screen_fingerprint': profile_obj.screen_fingerprint,
        }
    finally:
        db.close()

    # Proxy from pool (same as production)
    proxy_manager = ProxyManager()
    proxy_manager.load_proxies_from_db()
    pool_proxy = proxy_manager.get_available_proxy()
    if not pool_proxy:
        log.error("No proxy available")
        return 2
    proxy_data = {
        'host': pool_proxy['host'],
        'port': pool_proxy['port'],
        'username': pool_proxy['username'],
        'password': pool_proxy['password'],
        'proxy_type': pool_proxy.get('proxy_type', 'http'),
    }
    log.info(f"Proxy: {proxy_data['host']}:{proxy_data['port']}")

    banner("STEP 1: build profile_data + launch browser (same as production)")
    pg = ProfileGenerator()
    is_mobile = profile_data_from_db.get('is_mobile', False)
    profile_data = pg.generate_profile(profile_data_from_db['name'], is_mobile=is_mobile)
    profile_data.update({
        'user_agent': profile_data_from_db['user_agent'],
        'viewport': {
            'width': profile_data_from_db['viewport_width'],
            'height': profile_data_from_db['viewport_height'],
        },
        'timezone': profile_data_from_db['timezone'],
        'language': 'ru-RU',
        'platform': profile_data_from_db.get('platform') or profile_data.get('platform', 'Win32'),
        'images_enabled': True,
    })
    if profile_data_from_db.get('webgl_fingerprint'):
        try:
            wf = profile_data_from_db['webgl_fingerprint']
            if isinstance(wf, str):
                wf = json.loads(wf)
            if isinstance(wf, dict) and 'unmaskedVendor' in wf:
                profile_data['webgl_fingerprint'] = wf
        except Exception:
            pass
    for k in ('canvas_fingerprint', 'audio_fingerprint'):
        if profile_data_from_db.get(k):
            profile_data[k] = profile_data_from_db[k]
    sf = profile_data_from_db.get('screen_fingerprint')
    if sf and isinstance(sf, dict):
        for _k in ('css_media', 'feature_flags', 'audio_properties', 'speech_voices',
                   'sensor', 'connection_info', 'storage_quota', 'heap_size',
                   'system_colors', 'system_fonts', 'codecs', 'keyboard_layout', 'fonts',
                   'hardware_concurrency', 'device_memory', 'max_touch_points', 'do_not_track'):
            if _k in sf:
                profile_data[_k] = sf[_k]

    bm = BrowserManager()
    t0 = time.monotonic()
    try:
        browser_id = bm.create_browser_session(profile_data, proxy_data)
    except Exception as e:
        log.error(f"💀 create_browser_session failed: {type(e).__name__}: {e}")
        log.error(traceback.format_exc())
        return 3
    driver = bm.active_browsers[browser_id]
    log.info(f"✅ Browser up ({time.monotonic()-t0:.1f}s), browser_id={browser_id}")

    rc = 5
    diag_dir = "/tmp/yaru_diag"
    os.makedirs(diag_dir, exist_ok=True)
    stem = f"{diag_dir}/p{profile_obj.id}_{int(time.time())}"

    try:
        banner("STEP 2: navigate to https://ya.ru/")
        driver.set_page_load_timeout(40)
        t1 = time.monotonic()
        try:
            driver.get("https://ya.ru/")
        except Exception as e:
            log.error(f"⚠️ get(ya.ru) raised: {e}")
        load_time = time.monotonic() - t1

        try:
            url_now = driver.current_url
        except Exception as e:
            url_now = f"<error: {e}>"
        try:
            title_now = driver.title
        except Exception as e:
            title_now = f"<error: {e}>"

        log.info(f"📋 After load ({load_time:.1f}s): URL={url_now}")
        log.info(f"📋 Title='{title_now}'")

        # Detect chrome-error
        if str(url_now).startswith("chrome-error://"):
            log.error("🌐 Chrome network-error page (proxy/DNS issue). Aborting.")
            try:
                driver.save_screenshot(f"{stem}_error.png")
                log.info(f"   📸 saved {stem}_error.png")
            except Exception:
                pass
            rc = 6
            return rc

        # ── Save initial screenshot + html ──
        # NOTE: do this AFTER selector check — Page.screenshot/Page.content
        # sometimes kills the renderer on Yandex pages.
        # We'll just record what we have so far via JS first.
        banner("STEP 3: enumerate every <input> / <textarea> on the page (JS, lightweight)")
        try:
            info = driver.execute_script("""
                var els = document.querySelectorAll('input, textarea');
                var out = [];
                for (var i = 0; i < els.length; i++) {
                    var el = els[i];
                    var r = el.getBoundingClientRect();
                    out.push({
                        idx: i,
                        tag: el.tagName,
                        type: el.type || '',
                        name: el.name || '',
                        id: el.id || '',
                        cls: (el.className || '').substring(0, 120),
                        aria: el.getAttribute('aria-label') || '',
                        placeholder: el.placeholder || '',
                        role: el.getAttribute('role') || '',
                        w: Math.round(r.width),
                        h: Math.round(r.height),
                        x: Math.round(r.x),
                        y: Math.round(r.y),
                        visible: el.offsetParent !== null
                    });
                }
                return {
                    url: location.href,
                    title: document.title,
                    ready: document.readyState,
                    forms: document.querySelectorAll('form').length,
                    bodyLen: (document.body && document.body.innerHTML || '').length,
                    inputs: out
                };
            """)
        except Exception as e:
            log.error(f"JS inspect failed: {e}")
            info = None

        if info:
            log.info(f"   url={info.get('url')!r}")
            log.info(f"   title={info.get('title')!r} ready={info.get('ready')} "
                     f"forms={info.get('forms')} body_len={info.get('bodyLen')}")
            inputs = info.get("inputs") or []
            log.info(f"   total inputs/textareas on page: {len(inputs)}")
            for el in inputs:
                visible_mark = "👁" if el["visible"] else " "
                log.info(
                    f"   {visible_mark} #{el['idx']:02d} <{el['tag']} type={el['type']!r:>10} "
                    f"name={el['name']!r:>10} id={el['id']!r:>15} "
                    f"role={el['role']!r:>10} cls={el['cls'][:60]!r:>60}> "
                    f"size={el['w']}x{el['h']} pos={el['x']},{el['y']}"
                )
                if el["aria"] or el["placeholder"]:
                    log.info(f"        aria={el['aria']!r} placeholder={el['placeholder']!r}")

        # ── Try the production selector list ──
        banner("STEP 4: try every production selector against the page")
        from core.playwright_driver import By
        hits = []
        for sel in INPUT_SELECTORS:
            try:
                elems = driver.find_elements(By.CSS_SELECTOR, sel)
            except Exception as e:
                log.info(f"   ❌ {sel!r:<50} threw: {e}")
                continue
            visible_count = 0
            for el in elems:
                try:
                    if el.is_displayed():
                        visible_count += 1
                except Exception:
                    pass
            if elems:
                marker = "✅" if visible_count else "⚠️ "
                log.info(f"   {marker} {sel!r:<50} matched {len(elems)} (visible={visible_count})")
                if visible_count:
                    hits.append((sel, visible_count, len(elems)))
            else:
                log.info(f"   ·  {sel!r:<50} 0 matches")

        banner("STEP 5: verdict")
        if hits:
            log.info(f"🎉 PASS: {len(hits)} selector(s) found a VISIBLE search input")
            for sel, vis, total in hits:
                log.info(f"   ✅ {sel!r} → {vis} visible / {total} total")
            rc = 0
        else:
            log.error("❌ FAIL: no production selector found a visible input")
            log.error("   But the JS dump above shows what IS on the page.")
            log.error("   Compare visible inputs vs the selectors that should match them.")
            rc = 5

        # Save final screenshot + html (after selector check; if these kill
        # the renderer we don't care anymore)
        try:
            driver.save_screenshot(f"{stem}_final.png")
            log.info(f"📸 final screenshot: {stem}_final.png")
        except Exception as e:
            log.warning(f"screenshot fail: {e}")
        try:
            html = driver.page_source or ""
            with open(f"{stem}_final.html", "w", encoding="utf-8") as f:
                f.write(html)
            log.info(f"📄 html: {stem}_final.html ({len(html)} bytes)")
        except Exception as e:
            log.warning(f"html dump fail: {e}")

    finally:
        banner("CLEANUP")
        try:
            bm.close_browser_session(browser_id)
            log.info("✅ browser closed")
        except Exception as e:
            log.warning(f"close failed: {e}")

    return rc


if __name__ == "__main__":
    try:
        rc = main()
    except KeyboardInterrupt:
        rc = 130
    except Exception as e:
        log.error(f"💀 UNHANDLED: {type(e).__name__}: {e}")
        log.error(traceback.format_exc())
        rc = 99
    log.info(f"=== EXIT rc={rc} at {datetime.utcnow().isoformat()}Z ===")
    sys.exit(rc)
