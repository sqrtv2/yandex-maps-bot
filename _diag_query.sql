-- Fail reason categories (last 24h)
SELECT 
    CASE 
        WHEN error_message ILIKE '%showcaptcha%' THEN 'showcaptcha'
        WHEN error_message ILIKE '%captcha%' THEN 'other_captcha'
        WHEN error_message ILIKE '%Watchdog%' OR error_message ILIKE '%in_progress%' THEN 'watchdog_stuck'
        WHEN error_message ILIKE '%timeout%' OR error_message ILIKE '%Timed out%' THEN 'timeout'
        WHEN error_message ILIKE '%Browser died%' OR error_message ILIKE '%closed%' OR error_message ILIKE '%TargetClosed%' THEN 'browser_death'
        WHEN error_message ILIKE '%not found%' THEN 'not_found'
        WHEN error_message ILIKE '%memory%' OR error_message ILIKE '%OOM%' THEN 'memory'
        WHEN error_message ILIKE '%proxy%' THEN 'proxy'
        WHEN error_message IS NULL OR error_message = '' THEN 'no_reason'
        ELSE 'other'
    END as category,
    COUNT(*) as cnt,
    LEFT(MIN(error_message), 120) as sample
FROM tasks 
WHERE task_type = 'yandex_search' AND status = 'failed'
AND created_at >= NOW() - INTERVAL '24 hours'
GROUP BY category
ORDER BY cnt DESC;
