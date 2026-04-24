#!/usr/bin/env python3
"""
Camoufox A/B research — Этап 0.

Запускает camoufox через мобильный прокси и проверяет:
  1. Открывается ли yandex.ru/internet (показывает наш TLS/JA3 fingerprint)
  2. Открывается ли yandex.ru/search/?text=... без редиректа на showcaptcha
  3. browserscan.net basic-detect (опционально)

Запуск НА ПРОДЕ (мобильные прокси доступны только оттуда):
    cd /root/camoufox-test && source venv/bin/activate
    python _test_camoufox.py

Не трогает прод-контейнеры. Использует Xvfb если есть display, иначе headless.
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

# 4 активных мобильных прокси из БД (sqrtv2:21607141)
PROXIES = [
    {"server": "http://95.31.178.33:4055", "username": "sqrtv2", "password": "21607141"},
    {"server": "http://95.31.178.33:4054", "username": "sqrtv2", "password": "21607141"},
    {"server": "http://95.31.170.9:4102",  "username": "sqrtv2", "password": "21607141"},
    {"server": "http://95.31.170.9:4097",  "username": "sqrtv2", "password": "21607141"},
]

QUERIES = [
    "кофе москва",
    "пиццерия рядом",
    "автосервис спб",
    "стоматология цены",
    "ремонт айфона",
]

OUT_DIR = Path("/tmp/camoufox_test")
OUT_DIR.mkdir(exist_ok=True)


def run_one(proxy: dict, query: str, idx: int) -> dict:
    """Open yandex.ru/internet then yandex.ru/search and report result."""
    from camoufox.sync_api import Camoufox

    result = {
        "idx": idx,
        "proxy": proxy["server"],
        "query": query,
        "external_ip": None,
        "ja3": None,
        "search_url": None,
        "search_title": None,
        "captcha": None,
        "error": None,
    }

    headless = os.environ.get("DISPLAY") in (None, "")
    try:
        with Camoufox(
            headless=headless,
            humanize=True,
            os=("windows", "macos"),
            locale="ru-RU",
            proxy=proxy,
            geoip=True,
            i_know_what_im_doing=True,
        ) as browser:
            page = browser.new_page()

            # 1. Internet check — показывает наш JA3 и external IP
            try:
                page.goto("https://yandex.ru/internet/", wait_until="domcontentloaded", timeout=45000)
                time.sleep(3)
                # вытащим IP и JA3 если получится
                try:
                    body_txt = page.inner_text("body", timeout=5000)
                    # IP обычно в одном из <span> с классом наподобие "Ip__address"
                    import re
                    m_ip = re.search(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", body_txt)
                    if m_ip:
                        result["external_ip"] = m_ip.group(1)
                    m_ja3 = re.search(r"\b([0-9a-f]{32})\b", body_txt)
                    if m_ja3:
                        result["ja3"] = m_ja3.group(1)
                except Exception:
                    pass
                page.screenshot(path=str(OUT_DIR / f"{idx:02d}_internet.png"), full_page=False)
            except Exception as e:
                result["error"] = f"yandex.ru/internet failed: {e}"

            # 2. Search check — главный тест
            try:
                search_url = f"https://yandex.ru/search/?text={query.replace(' ', '+')}"
                page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
                time.sleep(4)
                cur = page.url
                title = page.title()
                result["search_url"] = cur
                result["search_title"] = title
                low = cur.lower()
                if "showcaptcha" in low or "captcha" in low:
                    result["captcha"] = True
                elif "/search/" in low:
                    result["captcha"] = False
                else:
                    result["captcha"] = "unknown"
                page.screenshot(path=str(OUT_DIR / f"{idx:02d}_search.png"), full_page=False)
            except Exception as e:
                result["error"] = (result["error"] or "") + f"; search failed: {e}"

    except Exception as e:
        result["error"] = f"launch failed: {e}\n{traceback.format_exc()}"
    return result


def _worker_main():
    """Subprocess entrypoint: read proxy+query from argv, print json result."""
    import json as _json
    proxy = _json.loads(sys.argv[2])
    query = sys.argv[3]
    idx = int(sys.argv[4])
    r = run_one(proxy, query, idx)
    sys.stdout.write("__RESULT__" + _json.dumps(r) + "\n")
    sys.stdout.flush()


def main():
    # Worker mode (re-entry as subprocess so each launch is in fresh process —
    # camoufox/playwright sync API leaves an asyncio loop alive that breaks
    # subsequent launches in the same process).
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        return _worker_main()

    import json as _json
    import subprocess

    print(f"=== Camoufox A/B research — {len(PROXIES)} proxies × {len(QUERIES)} queries ===")
    print(f"Output dir: {OUT_DIR}")
    print()

    runs = []
    idx = 0
    for proxy in PROXIES:
        for query in QUERIES:
            idx += 1
            print(f"[{idx:02d}] proxy={proxy['server']}  query={query!r}")
            try:
                proc = subprocess.run(
                    [sys.executable, __file__, "--worker", _json.dumps(proxy), query, str(idx)],
                    capture_output=True, text=True, timeout=120,
                )
                line = ""
                for ln in proc.stdout.splitlines():
                    if ln.startswith("__RESULT__"):
                        line = ln[len("__RESULT__"):]
                        break
                if line:
                    r = _json.loads(line)
                else:
                    r = {"idx": idx, "proxy": proxy["server"], "query": query,
                         "external_ip": None, "ja3": None, "search_url": None,
                         "search_title": None, "captcha": None,
                         "error": f"no result; stderr tail={proc.stderr[-200:]}"}
            except Exception as e:
                r = {"idx": idx, "proxy": proxy["server"], "query": query,
                     "external_ip": None, "ja3": None, "search_url": None,
                     "search_title": None, "captcha": None,
                     "error": f"subprocess failed: {e}"}
            runs.append(r)
            short_err = (r["error"] or "")[:120]
            print(
                f"     ip={r['external_ip']}  captcha={r['captcha']}  "
                f"title={(r['search_title'] or '')[:60]!r}  err={short_err!r}"
            )

    # Summary
    print()
    print("=== SUMMARY ===")
    total = len(runs)
    captcha = sum(1 for r in runs if r["captcha"] is True)
    ok = sum(1 for r in runs if r["captcha"] is False)
    failed = sum(1 for r in runs if r["error"])
    print(f"  total:   {total}")
    print(f"  ok:      {ok}  ({ok*100//max(total,1)}%)")
    print(f"  captcha: {captcha}  ({captcha*100//max(total,1)}%)")
    print(f"  errors:  {failed}")
    print()
    print("Per-proxy IP rotation:")
    by_proxy = {}
    for r in runs:
        by_proxy.setdefault(r["proxy"], set()).add(r["external_ip"])
    for p, ips in by_proxy.items():
        print(f"  {p}: ips={sorted(i for i in ips if i)}")


if __name__ == "__main__":
    sys.exit(main())
