#!/bin/bash
# Local test runner: headless=off, no proxy, 3 workers
# Requires SSH tunnel: ssh -f -N -L 15432:172.18.0.3:5432 root@88.99.146.218

export YANDEX_BOT_DATABASE_URL="postgresql://postgres:password@127.0.0.1:15432/yandex_maps_bot"
export YANDEX_BOT_REDIS_HOST="localhost"
export YANDEX_BOT_REDIS_PORT="6379"
export YANDEX_BOT_BROWSER_HEADLESS="false"
export YANDEX_BOT_DEBUG="true"

echo "🚀 Starting local Celery worker (3 threads, headless=OFF, no proxy)"
echo "   DB: postgresql://127.0.0.1:15432/yandex_maps_bot (via SSH tunnel)"
echo "   Redis: localhost:6379"
echo ""

python3 -m celery -A tasks.celery_app.celery_app worker \
    --loglevel=info \
    --concurrency=3 \
    --queues=yandex_search \
    -n local_test@%h \
    --without-heartbeat \
    --without-mingle
