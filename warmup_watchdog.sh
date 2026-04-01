#!/bin/bash
# warmup_watchdog.sh — Run via cron on the HOST (not inside container)
# Checks if warmup workers are actually completing tasks.
# If zero completions in the past N minutes, restarts warmup containers.
#
# Install: crontab -e
#   */15 * * * * /root/yandex-maps-bot/warmup_watchdog.sh >> /var/log/warmup_watchdog.log 2>&1

set -euo pipefail

COMPOSE_DIR="/root/yandex-maps-bot"
REDIS_CONTAINER="yandex_maps_redis"
STALE_THRESHOLD=3          # consecutive failed checks before restart
CHECK_WINDOW_MINUTES=15    # matches the cron interval
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

# Check queue length
queue_len=$(docker exec "$REDIS_CONTAINER" redis-cli LLEN warmup 2>/dev/null || echo "0")
queue_len=${queue_len:-0}

if [ "$new_completions" -gt 0 ] || [ "$queue_len" -eq 0 ]; then
    # Healthy or nothing to do
    docker exec "$REDIS_CONTAINER" redis-cli SET "$HOST_STALE_KEY" 0 > /dev/null 2>&1
    log "OK: +${new_completions} completions, queue=${queue_len}"
    exit 0
fi

# No progress — increment stale counter
stale=$(docker exec "$REDIS_CONTAINER" redis-cli INCR "$HOST_STALE_KEY" 2>/dev/null || echo "1")
log "WARNING: 0 completions (check #${stale}/${STALE_THRESHOLD}), queue=${queue_len}"

if [ "$stale" -ge "$STALE_THRESHOLD" ]; then
    log "CRITICAL: ${stale} consecutive stale checks — restarting warmup containers"
    
    cd "$COMPOSE_DIR"
    docker compose restart celery_warmup celery_warmup2 2>&1 | while read -r line; do log "$line"; done
    
    # Reset counter
    docker exec "$REDIS_CONTAINER" redis-cli SET "$HOST_STALE_KEY" 0 > /dev/null 2>&1
    
    log "Warmup containers restarted"
fi
