#!/bin/bash
# Restart Celery workers and beat with new code

echo "🔄 ПЕРЕЗАПУСК CELERY СИСТЕМЫ"
echo "========================================"
echo ""

# Stop all Celery processes
echo "⏹️  Останавливаем все процессы Celery..."
pkill -9 -f 'celery' 2>/dev/null
sleep 2

# Check if stopped
RUNNING=$(ps aux | grep '[c]elery' | wc -l | tr -d ' ')
if [ "$RUNNING" -gt "0" ]; then
    echo "⚠️  Некоторые процессы всё ещё работают, принудительно останавливаем..."
    killall -9 Python 2>/dev/null
    sleep 1
fi

echo "✅ Все процессы Celery остановлены"
echo ""

# Start Celery Worker
echo "🚀 Запускаем Celery Worker..."
cd /Users/sqrtv2/Project/PF
nohup python3 -m celery -A tasks.celery_app.celery_app worker \
    --loglevel=info \
    --concurrency=4 \
    --queues=default,warmup,yandex,proxy,maintenance \
    --logfile=logs/celery.log \
    --pidfile=logs/celery.pid \
    > logs/celery-worker-nohup.log 2>&1 &

WORKER_PID=$!
echo "✅ Worker запущен (PID: $WORKER_PID)"
sleep 2

# Start Celery Beat
echo "🔔 Запускаем Celery Beat..."
nohup python3 -m celery -A tasks.celery_app.celery_app beat \
    --loglevel=info \
    --logfile=logs/celery-beat.log \
    --pidfile=logs/celery-beat.pid \
    > logs/celery-beat-nohup.log 2>&1 &

BEAT_PID=$!
echo "✅ Beat запущен (PID: $BEAT_PID)"
sleep 2

# Verify
echo ""
echo "🔍 Проверка запущенных процессов:"
ps aux | grep '[c]elery' | awk '{print "   " $2 " - " $11 " " $12 " " $13}'

echo ""
echo "=" * 50
echo "✅ Celery система перезапущена!"
echo "=" * 50
echo ""
echo "📝 Логи:"
echo "   Worker: tail -f logs/celery.log"
echo "   Beat:   tail -f logs/celery-beat.log"
echo ""
echo "🧪 Тестирование:"
echo "   python3 check_scheduler.py"
echo "   python3 test_visit_medsemya.py"
echo ""
