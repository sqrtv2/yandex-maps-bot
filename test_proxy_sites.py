#!/usr/bin/env python3
"""Test: open various sites through proxy in Chrome."""
from playwright.sync_api import sync_playwright
import time

p = sync_playwright().start()
proxy = {
    "server": "http://95.31.170.9:4097",
    "username": "sqrtv2",
    "password": "21607141",
}
ctx = p.chromium.launch_persistent_context(
    user_data_dir="/tmp/test_proxy_sites",
    headless=True,
    proxy=proxy,
    viewport={"width": 1366, "height": 768},
    ignore_https_errors=True,
)
page = ctx.pages[0] if ctx.pages else ctx.new_page()

sites = [
    "https://google.com",
    "https://mail.ru",
    "https://ya.ru",
    "https://yandex.ru",
    "https://wikipedia.org",
]
for url in sites:
    try:
        page.goto(url, timeout=20000)
        time.sleep(2)
        title = page.title()
        print(f"{url} => Title: {title!r}, URL: {page.url}")
    except Exception as e:
        err = str(e).split('\n')[0]
        print(f"{url} => ERROR: {err}")

ctx.close()
p.stop()
print("Done.")
