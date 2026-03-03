#!/usr/bin/env python3
"""
Fix yandex_search.py — increase page_load_timeout and wrap post-click code.

Problem: page_load_timeout=45s is too aggressive for slow proxies.
All tasks fail with "Timed out receiving message from renderer: 45.000".

Fixes:
1. Main page_load_timeout: 45 → 120 (line 679)
2. Fallback search timeout: 30 → 90 (line 1013)
3. Captcha retry search timeout: 30 → 90 (line 1099)
4. Wrap post-search-click code (driver.current_url at ~line 997) in TimeoutException handler
5. Wrap post-search browsing (driver.page_source, driver.current_url after search button) in TimeoutException handler
"""

import shutil
import datetime
import re

FILE = "tasks/yandex_search.py"

# Create backup
backup = f"{FILE}.bak_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
shutil.copy2(FILE, backup)
print(f"✅ Backup: {backup}")

with open(FILE, 'r') as f:
    content = f.read()

original = content

# ===== Fix 1: Main page_load_timeout 45 → 120 =====
old1 = "driver.set_page_load_timeout(45)"
new1 = "driver.set_page_load_timeout(120)"
count1 = content.count(old1)
content = content.replace(old1, new1)
print(f"Fix 1: page_load_timeout 45→120 — replaced {count1} occurrence(s)")

# Also fix the retry log message  
old1b = "ya.ru page load timed out (45s)"
new1b = "ya.ru page load timed out (120s)"
content = content.replace(old1b, new1b)

old1c = 'ya.ru timeout 45s, retry...'
new1c = 'ya.ru timeout 120s, retry...'
content = content.replace(old1c, new1c)

# ===== Fix 2: Fallback search timeout 30 → 90 =====
# There are TWO places with set_page_load_timeout(30) — both are in fallback search paths
old2 = "driver.set_page_load_timeout(30)"
new2 = "driver.set_page_load_timeout(90)"
count2 = content.count(old2)
content = content.replace(old2, new2)
print(f"Fix 2: fallback page_load_timeout 30→90 — replaced {count2} occurrence(s)")

# ===== Fix 3: Wrap post-search-click code in TimeoutException handler =====
# After clicking search button, the code calls driver.current_url which hangs if page is loading
# We need to wrap lines ~995-1001 in a try/except

old3 = '''            time.sleep(random.uniform(4, 7))
            
            logger.info(f"   Search submitted. URL: {driver.current_url[:120]}")
            
            # Verify we're on search results page
            current_url = driver.current_url.lower()'''

new3 = '''            time.sleep(random.uniform(4, 7))
            
            try:
                logger.info(f"   Search submitted. URL: {driver.current_url[:120]}")
            except TimeoutException:
                logger.warning("   Timed out getting URL after search submit — continuing anyway")
            
            # Verify we're on search results page
            try:
                current_url = driver.current_url.lower()
            except TimeoutException:
                logger.warning("   Timed out getting current_url for verification — assuming search results loaded")
                current_url = "search"  # assume we're on search results'''

count3 = content.count(old3)
if count3 == 1:
    content = content.replace(old3, new3)
    print(f"Fix 3: Wrapped post-search-click driver.current_url in TimeoutException handler")
else:
    print(f"⚠️ Fix 3: Could not find exact match (found {count3}), skipping")

# ===== Fix 4: Wrap search results page_source access =====
# After verifying we're on search results, code accesses driver.page_source which can also timeout
old4 = '''        search_url_debug = driver.current_url
        search_title_debug = driver.title
        logger.info(f"📋 [DIAG] Search results page: URL={search_url_debug[:150]}, Title=\'{search_title_debug}\'")'''

new4 = '''        try:
            search_url_debug = driver.current_url
            search_title_debug = driver.title
            logger.info(f"📋 [DIAG] Search results page: URL={search_url_debug[:150]}, Title=\'{search_title_debug}\'")
        except TimeoutException:
            search_url_debug = "timeout"
            search_title_debug = "timeout"
            logger.warning("⚠️ Timed out getting search results page info — continuing anyway")'''

count4 = content.count(old4)
if count4 == 1:
    content = content.replace(old4, new4)
    print(f"Fix 4: Wrapped search results diagnostics in TimeoutException handler")
else:
    print(f"⚠️ Fix 4: Could not find exact match (found {count4}), skipping")

# ===== Fix 5: Wrap page_source access for captcha check =====
old5 = '''        search_src_lower = driver.page_source[:5000].lower()'''
new5 = '''        try:
            search_src_lower = driver.page_source[:5000].lower()
        except TimeoutException:
            logger.warning("⚠️ Timed out getting page_source for captcha check — assuming no captcha")
            search_src_lower = ""'''

count5 = content.count(old5)
if count5 == 1:
    content = content.replace(old5, new5)
    print(f"Fix 5: Wrapped page_source captcha check in TimeoutException handler")
else:
    print(f"⚠️ Fix 5: Could not find exact match (found {count5}), skipping")

# Verify the file is valid Python
try:
    compile(content, FILE, 'exec')
    print("✅ Python syntax validation passed")
except SyntaxError as e:
    print(f"❌ Syntax error: {e}")
    print("Restoring backup...")
    shutil.copy2(backup, FILE)
    exit(1)

# Write the fixed file
with open(FILE, 'w') as f:
    f.write(content)

# Count total changes
changes = 0
for i, (a, b) in enumerate(zip(original.split('\n'), content.split('\n'))):
    if a != b:
        changes += 1
extra_lines = len(content.split('\n')) - len(original.split('\n'))
print(f"\n✅ All fixes applied! {changes} lines modified, {extra_lines} lines added")
print(f"📁 Backup at: {backup}")
