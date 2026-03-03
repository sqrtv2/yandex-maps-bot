#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fix watchdog PENDING_MAX_MINUTES: 15 -> 45 minutes.

Problem: scheduler creates 26 tasks at once, but only 2 workers.
Each task takes ~3 min. 26/2 * 3 = 39 min to process all.
The 15 min watchdog kills pending tasks before workers reach them.

Fix: Increase pending timeout to 45 min (enough for 26 tasks with 2 workers).
"""
import shutil
import datetime

FILE = "tasks/yandex_scheduler.py"
backup = FILE + ".bak_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
shutil.copy2(FILE, backup)
print("Backup: " + backup)

with open(FILE, "r") as f:
    content = f.read()

changes = 0

# Fix 1: _cleanup_stale_yandex_visit_tasks pending threshold
old1 = "pending_threshold = timedelta(minutes=15)"
new1 = "pending_threshold = timedelta(minutes=45)"
if old1 in content:
    content = content.replace(old1, new1)
    changes += 1
    print("Fix 1: _cleanup_stale pending 15->45 min")
else:
    print("Fix 1: NOT FOUND")

# Fix 2: error messages
old2a = ">15"
new2a = ">45"
c2 = content.count(old2a)
content = content.replace(old2a, new2a)
changes += c2
print("Fix 2: Updated >15 -> >45 in error messages (" + str(c2) + " occurrences)")

# Fix 3: queue_watchdog PENDING_MAX_MINUTES
old3 = "PENDING_MAX_MINUTES = 15"
new3 = "PENDING_MAX_MINUTES = 45"
if old3 in content:
    content = content.replace(old3, new3)
    changes += 1
    print("Fix 3: Watchdog PENDING_MAX_MINUTES 15->45")
else:
    print("Fix 3: NOT FOUND")

# Fix 4: IN_PROGRESS_MAX_MINUTES 10 -> 15
old4 = "IN_PROGRESS_MAX_MINUTES = 10"
new4 = "IN_PROGRESS_MAX_MINUTES = 15"
if old4 in content:
    content = content.replace(old4, new4)
    changes += 1
    print("Fix 4: Watchdog IN_PROGRESS_MAX_MINUTES 10->15")
else:
    print("Fix 4: NOT FOUND")

# Validate
try:
    compile(content, FILE, "exec")
    print("Syntax OK")
except SyntaxError as e:
    print("Syntax error: " + str(e))
    shutil.copy2(backup, FILE)
    exit(1)

with open(FILE, "w") as f:
    f.write(content)

print("\nDone! Applied " + str(changes) + " fixes.")
