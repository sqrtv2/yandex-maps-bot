#!/usr/bin/env python3
"""
Fix 3 issues:
1. Retry bug: Celery 5.x re-raises original exception (not MaxRetriesExceededError)
   when exc= is provided and max retries exceeded. Fix: catch Exception after Retry.
2. SEARCH_MAX_DURATION 420 -> 540 (match soft_time_limit)
"""

FILEPATH = '/root/yandex-maps-bot/tasks/yandex_search.py'

with open(FILEPATH, 'r') as f:
    content = f.read()

original = content

# ===== Fix 1: Import Retry from celery.exceptions =====
old_import = 'from celery.exceptions import SoftTimeLimitExceeded'
new_import = 'from celery.exceptions import SoftTimeLimitExceeded, Retry as CeleryRetry'
if 'CeleryRetry' not in content:
    assert content.count(old_import) == 1, f"Expected 1 occurrence of import, found {content.count(old_import)}"
    content = content.replace(old_import, new_import)
    print("Added CeleryRetry import")
else:
    print("CeleryRetry import already exists")

# ===== Fix 2: Fix all 3 retry handlers =====
# Browser dead retry
old1 = '            except self.MaxRetriesExceededError:\n                logger.error(f"Max retries exceeded for browser crash (profile {profile_id})")'
new1 = '            except CeleryRetry:\n                raise\n            except (self.MaxRetriesExceededError, Exception):\n                logger.error(f"Max retries exceeded for browser crash (profile {profile_id})")'

count1 = content.count(old1)
if count1 == 1:
    content = content.replace(old1, new1)
    print("Fixed browser crash retry handler")
elif new1 in content:
    print("Browser crash retry already fixed")
else:
    print(f"WARNING: browser crash retry not found (count={count1})")

# Captcha retry
old2 = '                except self.MaxRetriesExceededError:\n                    logger.error(f"Max retries exceeded for captcha failure (profile {profile_id})")'
new2 = '                except CeleryRetry:\n                    raise\n                except (self.MaxRetriesExceededError, Exception):\n                    logger.error(f"Max retries exceeded for captcha failure (profile {profile_id})")'

count2 = content.count(old2)
if count2 == 1:
    content = content.replace(old2, new2)
    print("Fixed captcha retry handler")
elif new2 in content:
    print("Captcha retry already fixed")
else:
    print(f"WARNING: captcha retry not found (count={count2})")

# ERR_TUNNEL retry
old3 = '            except self.MaxRetriesExceededError:\n                logger.error("Max retries exceeded for proxy tunnel failure")'
new3 = '            except CeleryRetry:\n                raise\n            except (self.MaxRetriesExceededError, Exception):\n                logger.error("Max retries exceeded for proxy tunnel failure")'

count3 = content.count(old3)
if count3 == 1:
    content = content.replace(old3, new3)
    print("Fixed ERR_TUNNEL retry handler")
elif new3 in content:
    print("ERR_TUNNEL retry already fixed")
else:
    print(f"WARNING: ERR_TUNNEL retry not found (count={count3})")

# ===== Fix 3: Increase SEARCH_MAX_DURATION =====
old_duration = 'SEARCH_MAX_DURATION = 420'
new_duration = 'SEARCH_MAX_DURATION = 540'
if old_duration in content:
    content = content.replace(old_duration, new_duration)
    print("SEARCH_MAX_DURATION: 420 -> 540")
elif 'SEARCH_MAX_DURATION = 540' in content:
    print("SEARCH_MAX_DURATION already 540")
else:
    print("WARNING: Could not find SEARCH_MAX_DURATION = 420")

# Write changes
if content != original:
    with open(FILEPATH, 'w') as f:
        f.write(content)
    print(f"\nAll changes written to {FILEPATH}")
    print(f"Total diff: {len(content) - len(original)} chars")
else:
    print("\nNo changes needed")
