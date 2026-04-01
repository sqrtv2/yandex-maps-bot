-- Last 15 failed tasks with details
SELECT id, profile_id, error_message, created_at, started_at, completed_at,
       LEFT(execution_logs, 500) as logs_head
FROM tasks 
WHERE task_type = 'yandex_search' AND status = 'failed'
AND created_at >= NOW() - INTERVAL '6 hours'
ORDER BY id DESC LIMIT 15;

-- Current in-progress tasks
SELECT id, profile_id, created_at, started_at, 
       EXTRACT(EPOCH FROM (NOW() - started_at))/60 as minutes_running
FROM tasks 
WHERE task_type = 'yandex_search' AND status = 'in_progress'
ORDER BY started_at ASC;

-- Success rate per hour (last 12h)
SELECT 
    date_trunc('hour', created_at) as hour,
    COUNT(*) FILTER (WHERE status = 'completed') as completed,
    COUNT(*) FILTER (WHERE status = 'failed') as failed,
    COUNT(*) FILTER (WHERE status = 'not_found') as not_found,
    COUNT(*) as total,
    ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'completed') / NULLIF(COUNT(*), 0), 1) as success_pct
FROM tasks 
WHERE task_type = 'yandex_search'
AND created_at >= NOW() - INTERVAL '12 hours'
GROUP BY hour
ORDER BY hour DESC;

-- Recent showcaptcha failures with logs
SELECT id, profile_id, error_message, 
       RIGHT(execution_logs, 500) as logs_tail
FROM tasks 
WHERE task_type = 'yandex_search' AND status = 'failed'
AND error_message ILIKE '%showcaptcha%'
AND created_at >= NOW() - INTERVAL '3 hours'
ORDER BY id DESC LIMIT 5;

-- Recent watchdog failures with logs
SELECT id, profile_id, error_message, started_at,
       EXTRACT(EPOCH FROM (completed_at - started_at))/60 as ran_minutes,
       RIGHT(execution_logs, 500) as logs_tail
FROM tasks 
WHERE task_type = 'yandex_search' AND status = 'failed'
AND error_message ILIKE '%Watchdog%'
AND created_at >= NOW() - INTERVAL '3 hours'
ORDER BY id DESC LIMIT 5;

-- ForeignKey errors detail
SELECT id, error_message, profile_id
FROM tasks 
WHERE task_type = 'yandex_search' AND status = 'failed'
AND error_message ILIKE '%ForeignKey%'
AND created_at >= NOW() - INTERVAL '24 hours'
ORDER BY id DESC LIMIT 5;
