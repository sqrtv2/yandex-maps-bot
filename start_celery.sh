#!/bin/bash
# Script to start Celery worker with correct configuration

echo "🚀 Starting Celery worker..."

# Kill existing Celery processes
pkill -9 -f "celery.*worker" 2>/dev/null

# Wait for processes to stop
sleep 2

# Read concurrency from config or use default (3 threads for warmup)
CONCURRENCY=${CELERY_WORKER_CONCURRENCY:-3}

echo "📊 Configuration:"
echo "   - Concurrency: $CONCURRENCY parallel workers"
echo "   - Queues: default,warmup,yandex_maps,yandex_search,proxy,maintenance"

# Start Celery worker with correct queues and concurrency from config
python3 -m celery -A tasks.celery_app.celery_app worker \
    --loglevel=info \
    --concurrency=$CONCURRENCY \
    --queues=default,warmup,yandex_maps,yandex_search,proxy,maintenance \
    --logfile=logs/celery.log \
    --pidfile=logs/celery.pid

echo "✅ Celery worker started with concurrency=$CONCURRENCY"
