#!/usr/bin/env python3
"""Remote diagnostics script - run inside Docker container"""
import sqlite3
import json
from datetime import datetime

conn = sqlite3.connect('/app/yandex_maps_bot.db')
conn.row_factory = sqlite3.Row

print("=== LAST 30 FAILED SEARCH TASKS ===")
rows = conn.execute("""
    SELECT id, status, error_message, created_at, started_at, completed_at, profile_id, execution_logs
    FROM tasks 
    WHERE task_type = 'yandex_search' AND status = 'failed'
    ORDER BY id DESC LIMIT 30
""").fetchall()
for r in rows:
    reason = (r["error_message"] or "None")[:150]
    logs = (r["execution_logs"] or "")[-200:]
    print(f'ID={r["id"]} profile={r["profile_id"]} reason={reason} created={r["created_at"]} started={r["started_at"]}')
    if logs:
        print(f'  logs_tail: {logs}')

print()
print("=== FAIL REASON STATS (last 24h) ===")
rows2 = conn.execute("""
    SELECT 
        CASE 
            WHEN error_message LIKE '%showcaptcha%' THEN 'showcaptcha'
            WHEN error_message LIKE '%captcha%' THEN 'other_captcha'
            WHEN error_message LIKE '%Watchdog%' OR error_message LIKE '%in_progress%' THEN 'watchdog_stuck'
            WHEN error_message LIKE '%timeout%' OR error_message LIKE '%Timed out%' THEN 'timeout'
            WHEN error_message LIKE '%Browser died%' OR error_message LIKE '%closed%' THEN 'browser_death'
            WHEN error_message LIKE '%not found%' THEN 'not_found'
            WHEN error_message IS NULL THEN 'no_reason'
            ELSE 'other: ' || substr(error_message, 1, 80)
        END as category,
        COUNT(*) as cnt
    FROM tasks 
    WHERE task_type = 'yandex_search' AND status = 'failed'
    AND created_at >= datetime('now', '-1 day')
    GROUP BY category
    ORDER BY cnt DESC
""").fetchall()
for r in rows2:
    print(f'  {r["category"]}: {r["cnt"]}')

print()
print("=== CURRENT IN-PROGRESS SEARCH TASKS ===")
rows3 = conn.execute("""
    SELECT id, status, created_at, started_at
    FROM tasks 
    WHERE task_type = 'yandex_search' AND status = 'in_progress'
    ORDER BY id DESC LIMIT 20
""").fetchall()
if not rows3:
    print("  (none)")
for r in rows3:
    print(f'ID={r["id"]} started={r["started_at"]}')

print()
print("=== PENDING SEARCH TASKS ===")
rows_p = conn.execute("""
    SELECT COUNT(*) as cnt FROM tasks 
    WHERE task_type = 'yandex_search' AND status = 'pending'
""").fetchone()
print(f'  pending: {rows_p["cnt"]}')

print()
print("=== TASK STATUS COUNTS (last 24h) ===")
rows4 = conn.execute("""
    SELECT status, COUNT(*) as cnt
    FROM tasks 
    WHERE task_type = 'yandex_search'
    AND created_at >= datetime('now', '-1 day')
    GROUP BY status
    ORDER BY cnt DESC
""").fetchall()
for r in rows4:
    print(f'  {r["status"]}: {r["cnt"]}')

print()
print("=== COMPLETED vs FAILED (last 24h) ===")
rows5 = conn.execute("""
    SELECT status, COUNT(*) as cnt
    FROM tasks 
    WHERE task_type = 'yandex_search'
    AND created_at >= datetime('now', '-1 day')
    AND status IN ('completed', 'failed')
    GROUP BY status
""").fetchall()
for r in rows5:
    print(f'  {r["status"]}: {r["cnt"]}')

print()
print("=== RECENT ERROR LOGS (last 20) ===")
try:
    rows6 = conn.execute("""
        SELECT id, error_type, error_message, created_at, profile_id
        FROM error_logs
        WHERE created_at >= datetime('now', '-1 day')
        ORDER BY id DESC LIMIT 20
    """).fetchall()
    for r in rows6:
        msg = (r["error_message"] or "")[:150]
        print(f'ID={r["id"]} type={r["error_type"]} profile={r["profile_id"]} msg={msg}')
except Exception as e:
    print(f"  Error reading error_logs: {e}")

print()
print("=== LAST 10 SEARCH TASK LOGS (with details) ===")
try:
    rows7 = conn.execute("""
        SELECT t.id, t.status, t.error_message, t.created_at, t.started_at, t.completed_at, 
               t.profile_id, t.parameters, t.execution_logs
        FROM tasks t
        WHERE t.task_type = 'yandex_search'
        ORDER BY t.id DESC LIMIT 10
    """).fetchall()
    for r in rows7:
        params = r["parameters"]
        keyword = proxy = "?"
        if params:
            try:
                pd = json.loads(params)
                keyword = pd.get("keyword", "?")
                proxy = pd.get("proxy", pd.get("proxy_url", "?"))
            except:
                pass
        reason = (r["error_message"] or "")[:120]
        logs = (r["execution_logs"] or "")[-400:]
        print(f'\nTASK #{r["id"]} [{r["status"]}] kw="{keyword}" profile={r["profile_id"]} proxy={proxy}')
        print(f'  created={r["created_at"]} started={r["started_at"]} completed={r["completed_at"]}')
        print(f'  reason: {reason}')
        if logs:
            print(f'  logs: {logs}')
except Exception as e:
    print(f"  Error: {e}")

conn.close()
