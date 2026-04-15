#!/bin/bash
# warmup_watchdog.sh — Run via cron on the HOST (not inside container)
# Checks if warmup workers are actually completing tasks.
# If zero completions in the past N minutes:
#   1. Purge clogged warmup queue
#   2. Run health check inside container
#   3. Restart warmup containers if needed
#
# Install: crontab -e
#   */10 * * * * /root/yandex-maps-bot/warmup_watchdog.sh >> /var/log/warmup_watchdog.log 2>&1

set -u  # don't use -e: we want the script to continue even if individual commands fail

COMPOSE_DIR="/root/yandex-maps-bot"
REDIS_CONTAINER="yandex_maps_redis"
APP_CONTAINER="yandex_maps_app"
STALE_THRESHOLD=2          # consecutive failed checks before restart
LOG_PREFIX="[warmup-watchdog]"

# Redis keys
COMPLETIONS_KEY="warmup:completions"
HOST_LAST_KEY="warmup:host_last_check"
HOST_STALE_KEY="warmup:host_stale_count"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $LOG_PREFIX $*"; }

# Get current completion count from Redis
current=$(docker exec "$REDIS_CONTAINER" redis-cli GET "$COMPLETIONS_KEY" 2>/dev/null || echo "0")
current=${current:-0}

# Get last seen count
last=$(docker exec "$REDIS_CONTAINER" redis-cli GET "$HOST_LAST_KEY" 2>/dev/null || echo "0")
last=${last:-0}

# Save current as last
docker exec "$REDIS_CONTAINER" redis-cli SET "$HOST_LAST_KEY" "$current" > /dev/null 2>&1

new_completions=$((current - last))

# Check queue lengths
warmup_queue=$(docker exec "$REDIS_CONTAINER" redis-cli LLEN warmup 2>/dev/null || echo "0")
warmup_queue=${warmup_queue:-0}
default_queue=$(docker exec "$REDIS_CONTAINER" redis-cli LLEN default 2>/dev/null || echo "0")
default_queue=${default_queue:-0}

# Check how many profiles are currently warming (via DB)
warming_count=$(docker exec "$APP_CONTAINER" python -c "
from app.database import SessionLocal
from sqlalchemy import text
db = SessionLocal()
r = db.execute(text(\"SELECT COUNT(*) FROM browser_profiles WHERE status='warming_up'\")).scalar()
print(r)
db.close()
" 2>/dev/null || echo "0")
warming_count=${warming_count:-0}

# Check needs warmup
needs_warmup=$(docker exec "$APP_CONTAINER" python -c "
from app.database import SessionLocal
from sqlalchemy import text
db = SessionLocal()
r = db.execute(text(\"SELECT COUNT(*) FROM browser_profiles WHERE warmup_completed=false AND is_active=true AND status='created'\")).scalar()
print(r)
db.close()
" 2>/dev/null || echo "0")
needs_warmup=${needs_warmup:-0}

log "Status: completions=+${new_completions}, warming=${warming_count}, needs=${needs_warmup}, warmup_q=${warmup_queue}, default_q=${default_queue}"

# FAST FIX: If queue > 200, purge immediately (likely full of dead tasks)
if [ "$warmup_queue" -gt 200 ]; then
    log "Queue clogged (${warmup_queue} tasks). Purging warmup queue..."
    docker exec "$REDIS_CONTAINER" redis-cli DEL warmup > /dev/null 2>&1
    warmup_queue=0
fi

# FAST FIX: If default queue clogged (scheduler tasks stuck)
if [ "$default_queue" -gt 50 ]; then
    log "Default queue clogged (${default_queue}). Purging..."
    docker exec "$REDIS_CONTAINER" redis-cli DEL default > /dev/null 2>&1
fi

if [ "$new_completions" -gt 0 ] || [ "$needs_warmup" -eq 0 ]; then
    # Healthy or nothing to do
    docker exec "$REDIS_CONTAINER" redis-cli SET "$HOST_STALE_KEY" 0 > /dev/null 2>&1
    log "OK"
    exit 0
fi

# No progress and profiles need warmup — something is wrong
stale=$(docker exec "$REDIS_CONTAINER" redis-cli INCR "$HOST_STALE_KEY" 2>/dev/null || echo "1")
log "WARNING: 0 completions, ${warming_count} warming, check #${stale}/${STALE_THRESHOLD}"

if [ "$stale" -ge "$STALE_THRESHOLD" ]; then
    log "CRITICAL: ${stale} consecutive stale checks — purging queue and restarting"
    
    # Purge warmup queue (may contain dead profile tasks)
    docker exec "$REDIS_CONTAINER" redis-cli DEL warmup > /dev/null 2>&1
    log "Purged warmup queue"
    
    # Run health check (may fail if file not in container — that's OK)
    docker exec "$APP_CONTAINER" python check_warmup_health.py 2>&1 | while read -r line; do log "  $line"; done || log "Health check script not available, skipping"
    
    # Reset stuck warming_up profiles directly via DB
    docker exec "$APP_CONTAINER" python -c "
from app.database import SessionLocal
from sqlalchemy import text
db = SessionLocal()
fixed = db.execute(text(\"UPDATE browser_profiles SET status='created', updated_at=NOW() WHERE status='warming_up' AND updated_at < NOW() - INTERVAL '10 minutes'\")).rowcount
db.commit()
if fixed: print(f'Reset {fixed} stuck warming_up profiles')
db.close()
" 2>&1 | while read -r line; do log "  $line"; done || true
    
    # Restart warmup workers
    cd "$COMPOSE_DIR"
    docker compose restart celery_warmup celery_warmup2 2>&1 | while read -r line; do log "  $line"; done || log "Failed to restart warmup containers"
    
    # Reset counter
    docker exec "$REDIS_CONTAINER" redis-cli SET "$HOST_STALE_KEY" 0 > /dev/null 2>&1
    
    log "Warmup containers restarted"
fi
