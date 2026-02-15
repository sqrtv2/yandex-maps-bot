#!/bin/bash
# Start Celery Beat scheduler for periodic tasks

cd /Users/sqrtv2/Project/PF

echo "🔔 Запуск Celery Beat (планировщик периодических задач)"
echo "========================================================"
echo ""
echo "📅 Периодические задачи:"
echo "  • Яндекс Карты посещения - каждые 5 минут"
echo "  • Проверка прокси - каждые 15 минут"
echo "  • Очистка старых задач - ежедневно в 2:00"
echo "  • Обслуживание профилей - ежедневно в 1:00"
echo ""
echo "🔍 Логи: logs/celery-beat.log"
echo ""

# Create logs directory if it doesn't exist
mkdir -p logs

# Remove stale PID file if the process is dead
if [ -f logs/celery-beat.pid ]; then
    OLD_PID=$(cat logs/celery-beat.pid 2>/dev/null)
    if [ -n "$OLD_PID" ] && ! kill -0 "$OLD_PID" 2>/dev/null; then
        echo "⚠️  Удалён устаревший PID-файл (процесс $OLD_PID не существует)"
        rm -f logs/celery-beat.pid
    fi
fi

# Start Celery Beat
python3 -m celery -A tasks.celery_app.celery_app beat \
    --loglevel=info \
    --logfile=logs/celery-beat.log \
    --pidfile=logs/celery-beat.pid
