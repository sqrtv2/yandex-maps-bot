from app.database import get_db_session
from sqlalchemy import text
from datetime import datetime

with get_db_session() as db:
    now = datetime.utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    rows = db.execute(text("""
        SELECT 
            CASE 
                WHEN error_message LIKE '%Watchdog: task timeout%' THEN 'Watchdog timeout (>10min)'
                WHEN error_message LIKE '%зависла в in_progress%' THEN 'Watchdog stuck in_progress'
                WHEN error_message LIKE '%Connection closed%' THEN 'Browser crash'
                WHEN error_message LIKE '%Captcha not solved%' THEN 'Captcha not solved'
                WHEN error_message LIKE '%Click failed%' THEN 'Click failed'
                WHEN error_message LIKE '%Search fallback%' THEN 'Proxy/renderer dead'
                WHEN error_message LIKE '%SoftTimeLimitExceeded%' THEN 'SoftTimeLimitExceeded'
                WHEN error_message LIKE '%не запустилась%' THEN 'Task stuck pending'
                WHEN error_message LIKE '%задача зависла%' THEN 'Task stuck cleanup'
                WHEN error_message LIKE '%Target not found%' THEN 'Target not found'
                WHEN error_message LIKE '%Proxy%' OR error_message LIKE '%proxy%' THEN 'Proxy error'
                WHEN error_message LIKE '%cleanup%' OR error_message LIKE '%очистк%' THEN 'Manual cleanup'
                ELSE SUBSTRING(error_message, 1, 60)
            END as error_type,
            COUNT(*) as cnt
        FROM tasks 
        WHERE task_type = 'yandex_search' 
            AND created_at >= :today
            AND status = 'failed'
        GROUP BY error_type
        ORDER BY cnt DESC
    """), {'today': today}).fetchall()
    
    total_failed = sum(r[1] for r in rows)
    completed = db.execute(text(
        "SELECT COUNT(*) FROM tasks WHERE task_type = 'yandex_search' AND created_at >= :today AND status = 'completed'"
    ), {'today': today}).scalar()
    in_progress = db.execute(text(
        "SELECT COUNT(*) FROM tasks WHERE task_type = 'yandex_search' AND created_at >= :today AND status = 'in_progress'"
    ), {'today': today}).scalar()
    pending = db.execute(text(
        "SELECT COUNT(*) FROM tasks WHERE task_type = 'yandex_search' AND created_at >= :today AND status = 'pending'"
    ), {'today': today}).scalar()
    
    total = completed + total_failed
    rate = completed * 100 / total if total else 0
    print(f'=== SEARCH TODAY ===')
    print(f'Completed: {completed} | Failed: {total_failed} | In-progress: {in_progress} | Pending: {pending}')
    print(f'Success rate: {rate:.1f}%')
    print(f'')
    print(f'=== ERROR BREAKDOWN ===')
    for r in rows:
        pct = r[1] * 100 / total_failed if total_failed else 0
        print(f'  {r[1]:4d} ({pct:5.1f}%) | {r[0]}')

    # Also check current load and watchdog timeout setting
    print()
    print('=== WATCHDOG/TIMEOUT CONFIG ===')
    
    # Check avg task duration for successful ones
    avg = db.execute(text("""
        SELECT AVG(EXTRACT(EPOCH FROM (completed_at - started_at))) as avg_sec,
               MAX(EXTRACT(EPOCH FROM (completed_at - started_at))) as max_sec,
               MIN(EXTRACT(EPOCH FROM (completed_at - started_at))) as min_sec
        FROM tasks 
        WHERE task_type = 'yandex_search' 
            AND created_at >= :today 
            AND status = 'completed'
            AND started_at IS NOT NULL
            AND completed_at IS NOT NULL
    """), {'today': today}).fetchone()
    if avg[0]:
        print(f'Successful task duration: avg={avg[0]:.0f}s, min={avg[2]:.0f}s, max={avg[1]:.0f}s')
    
    # Check avg for failed watchdog ones
    avg_fail = db.execute(text("""
        SELECT AVG(EXTRACT(EPOCH FROM (completed_at - started_at))) as avg_sec
        FROM tasks 
        WHERE task_type = 'yandex_search' 
            AND created_at >= :today 
            AND status = 'failed'
            AND error_message LIKE '%Watchdog: task timeout%'
            AND started_at IS NOT NULL
            AND completed_at IS NOT NULL
    """), {'today': today}).fetchone()
    if avg_fail[0]:
        print(f'Watchdog timeout tasks lived: avg={avg_fail[0]:.0f}s')
