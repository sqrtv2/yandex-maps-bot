"""
Camoufox warmup runner — RUNS IN A SUBPROCESS, NOT IN CELERY WORKER PROCESS.

Why subprocess: camoufox's sync API (via playwright) keeps a background asyncio
loop alive after the first launch. A second launch in the same process raises
"Playwright Sync API inside the asyncio loop". Process isolation is the
guaranteed fix and avoids any conflict with rebrowser/patchright sync_playwright
instances that may already be pinned in the celery worker.

Protocol:
  stdin   : JSON {profile_dir, proxy, sites, ua, timezone, locale, headless}
  stdout  : line "__RESULT__<json>" with {ok, sites_visited, elapsed, error}
  exit    : 0 on success, non-zero on launcher failure

Usage (launched by tasks/warmup_camoufox.py):
    proc = subprocess.run(
        [sys.executable, "-m", "core.camoufox_runner"],
        input=json.dumps(cfg), capture_output=True, text=True, timeout=...
    )
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
import traceback
from pathlib import Path


def _human_sleep(a: float, b: float) -> None:
    time.sleep(random.uniform(a, b))


def _scroll_a_bit(page) -> None:
    """Random light scrolls — no precise inputs needed for warmup."""
    try:
        steps = random.randint(2, 5)
        for _ in range(steps):
            dy = random.randint(200, 700)
            page.mouse.wheel(0, dy)
            _human_sleep(0.4, 1.2)
    except Exception:
        pass


def _visit_one(page, url: str) -> dict:
    t0 = time.time()
    info = {"url": url, "ok": False, "title": "", "final_url": "", "ms": 0, "err": None}
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        # gentle settle
        _human_sleep(1.5, 3.5)
        try:
            info["title"] = (page.title() or "")[:120]
            info["final_url"] = page.url
        except Exception:
            pass
        _scroll_a_bit(page)
        # Linger like a real user
        _human_sleep(2.0, 5.0)
        info["ok"] = True
    except Exception as e:
        info["err"] = f"{type(e).__name__}: {e}"
    info["ms"] = int((time.time() - t0) * 1000)
    return info


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        sys.stdout.write("__RESULT__" + json.dumps({"ok": False, "error": "empty stdin"}) + "\n")
        return 2
    try:
        cfg = json.loads(raw)
    except Exception as e:
        sys.stdout.write("__RESULT__" + json.dumps({"ok": False, "error": f"bad json: {e}"}) + "\n")
        return 2

    profile_dir = cfg["profile_dir"]
    proxy = cfg.get("proxy")
    sites = list(cfg.get("sites") or [])
    ua = cfg.get("user_agent")
    timezone_id = cfg.get("timezone") or "Europe/Moscow"
    locale = cfg.get("locale") or "ru-RU"
    headless = bool(cfg.get("headless", True))
    os_choice = cfg.get("os") or ("windows", "macos")  # camoufox FP family
    geoip = bool(cfg.get("geoip", True))
    humanize = bool(cfg.get("humanize", True))

    Path(profile_dir).mkdir(parents=True, exist_ok=True)

    result = {
        "ok": False,
        "profile_dir": profile_dir,
        "sites_total": len(sites),
        "sites_visited": 0,
        "sites_failed": 0,
        "elapsed_s": 0,
        "details": [],
        "error": None,
    }

    t_start = time.time()
    try:
        # IMPORTANT: bypass `camoufox.sync_api.Camoufox` because it imports
        # `playwright.sync_api`, and on this server the `playwright` package
        # is a shim that re-exports rebrowser_playwright. rebrowser_playwright
        # 1.52's Node driver crashes with Firefox in `_onClearLifecycle`
        # (TypeError: Cannot read properties of undefined ('get')) on first
        # navigation, killing the browser process with SIGKILL before we ever
        # reach a single page.
        #
        # We use `patchright` instead (clean playwright fork compatible with
        # Firefox), feeding it the launch_options dict that camoufox builds
        # for its anti-detect Firefox bundle.
        from patchright.sync_api import sync_playwright
        from camoufox.utils import launch_options as _cf_launch_options

        opts = _cf_launch_options(
            headless=headless,
            humanize=humanize,
            os=os_choice,
            locale=locale,
            geoip=geoip,
            i_know_what_im_doing=True,
            user_data_dir=profile_dir,
        )
        # `launch_options` may include keys that launch_persistent_context()
        # does not accept. Strip them.
        opts.pop("persistent_context", None)
        if proxy:
            opts["proxy"] = proxy

        # Patchright's node driver occasionally fails to initialise under
        # parallel load with: "Connection.init: Connection closed while
        # reading from the driver". The failure is non-deterministic, so
        # retry the whole sync_playwright()+launch sequence a few times with
        # exponential backoff before giving up.
        last_init_err = None
        ctx = None
        _pw_cm = None
        for attempt in range(3):
            try:
                _pw_cm = sync_playwright().__enter__()
                ctx = _pw_cm.firefox.launch_persistent_context(**opts)
                last_init_err = None
                break
            except Exception as e:
                last_init_err = e
                # tear down half-initialised playwright before retry
                try:
                    if _pw_cm is not None:
                        _pw_cm.stop()
                except Exception:
                    pass
                _pw_cm = None
                msg = str(e)
                if (
                    "Connection closed while reading from the driver" in msg
                    or "_playwright" in msg
                ):
                    time.sleep(0.7 * (attempt + 1) + random.uniform(0, 0.6))
                    continue
                raise
        if ctx is None:
            raise last_init_err if last_init_err else RuntimeError(
                "camoufox: failed to start patchright after 3 attempts"
            )

        try:
            # New page (or reuse existing one persistent contexts may open)
            try:
                page = ctx.pages[0] if getattr(ctx, "pages", None) else ctx.new_page()
            except Exception:
                page = ctx.new_page()

            try:
                page.set_default_timeout(30000)
                page.set_default_navigation_timeout(30000)
            except Exception:
                pass

            consec_fail = 0
            for url in sites:
                info = _visit_one(page, url)
                result["details"].append(info)
                if info["ok"]:
                    result["sites_visited"] += 1
                    consec_fail = 0
                else:
                    result["sites_failed"] += 1
                    consec_fail += 1
                if consec_fail >= 4:
                    result["error"] = f"4 consecutive failures, last={info['err']}"
                    break
                _human_sleep(0.8, 2.0)
        finally:
            try:
                ctx.close()
            except Exception:
                pass
            try:
                _pw_cm.stop()
            except Exception:
                pass

        result["ok"] = result["sites_visited"] > 0
    except Exception as e:
        result["error"] = f"launcher: {type(e).__name__}: {e}"
        tb = traceback.format_exc()
        result["traceback"] = tb
        # Mirror full traceback to stderr so the celery task can surface it
        sys.stderr.write("\n=== camoufox_runner traceback ===\n")
        sys.stderr.write(tb)
        sys.stderr.write("=== /traceback ===\n")
        sys.stderr.flush()

    result["elapsed_s"] = round(time.time() - t_start, 1)
    sys.stdout.write("__RESULT__" + json.dumps(result, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
