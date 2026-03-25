#!/bin/bash

# Скрипт деплоя оптимизаций для достижения 1000 кликов/день
# Использование: bash deploy_optimizations.sh

set -e

SERVER_IP="88.99.146.218"
SERVER_USER="root"
SERVER_PATH="/root/yandex-maps-bot"

echo "🚀 Deploying optimizations for 1000 clicks/day to $SERVER_IP"

# 1. Остановить текущие контейнеры
echo "⏹️  Stopping current containers..."
ssh $SERVER_USER@$SERVER_IP "cd $SERVER_PATH && docker-compose down"

# 2. Создать бэкап текущей конфигурации
echo "💾 Creating backup..."
ssh $SERVER_USER@$SERVER_IP "cd $SERVER_PATH && cp docker-compose.yml docker-compose.yml.backup.$(date +%Y%m%d_%H%M%S)"

# 3. Загрузить новую оптимизированную конфигурацию
echo "📝 Uploading optimized configuration..."
scp docker-compose-optimized.yml $SERVER_USER@$SERVER_IP:$SERVER_PATH/docker-compose.yml

# 4. Загрузить оптимизированный browser_manager
echo "🔧 Uploading optimized browser manager..."
scp core/browser_manager_optimized.py $SERVER_USER@$SERVER_IP:$SERVER_PATH/core/browser_manager_optimized.py

# 5. Создать скрипт очистки процессов
echo "🧹 Creating cleanup script..."
ssh $SERVER_USER@$SERVER_IP "cat > $SERVER_PATH/cleanup_chrome.sh << 'EOF'
#!/bin/bash
# Агрессивная очистка Chrome процессов

echo \"Killing Chrome processes...\"
pkill -9 -f \"chrome.*--headless\" || true
pkill -9 -f \"chromium.*--headless\" || true

# Убить старые процессы (>5 минут)
ps -eo pid,etimes,comm | grep -E 'chrom|playwright' | awk '\$2 > 300 {print \$1}' | while read pid; do
  if [[ \$pid =~ ^[0-9]+$ ]]; then
    kill -9 \$pid 2>/dev/null || true
    echo \"Killed old process: \$pid\"
  fi
done

# Очистка /tmp от профилей
find /tmp -name \"test_*\" -type d -mtime +1 -exec rm -rf {} + 2>/dev/null || true

echo \"Chrome cleanup completed\"
EOF"

ssh $SERVER_USER@$SERVER_IP "chmod +x $SERVER_PATH/cleanup_chrome.sh"

# 6. Создать скрипт мониторинга
echo "📊 Creating monitoring script..."
ssh $SERVER_USER@$SERVER_IP "cat > $SERVER_PATH/monitor_performance.sh << 'EOF'
#!/bin/bash
# Мониторинг производительности системы

echo \"=== $(date) ===\"
echo \"Docker containers:\"
docker ps --format \"table {{.Names}}\t{{.Status}}\t{{.Ports}}\"

echo -e \"\nResource usage:\"
docker stats --no-stream --format \"table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\"

echo -e \"\nCelery workers status:\"
docker exec yandex-maps-bot-celery_yandex_search-1 celery -A tasks.celery_app inspect active 2>/dev/null | head -20 || echo \"Worker not responding\"

echo -e \"\nSystem memory:\"
free -h

echo -e \"\nChrome processes:\"
ps aux | grep -E 'chrom|playwright' | wc -l
echo \"Chrome processes count: $(ps aux | grep -E 'chrom|playwright' | wc -l)\"

echo -e \"\nLast 5 search worker logs:\"
docker logs --tail=5 yandex-maps-bot-celery_yandex_search-1

echo \"===========================================\"
EOF"

ssh $SERVER_USER@$SERVER_IP "chmod +x $SERVER_PATH/monitor_performance.sh"

# 7. Пересобрать образы с новой конфигурацией
echo "🔨 Rebuilding Docker images..."
ssh $SERVER_USER@$SERVER_IP "cd $SERVER_PATH && docker-compose build"

# 8. Запустить оптимизированную систему
echo "🚀 Starting optimized system..."
ssh $SERVER_USER@$SERVER_IP "cd $SERVER_PATH && docker-compose up -d"

# 9. Ожидание запуска сервисов
echo "⏳ Waiting for services to start..."
sleep 30

# 10. Проверить статус
echo "🔍 Checking system status..."
ssh $SERVER_USER@$SERVER_IP "cd $SERVER_PATH && docker-compose ps"

# 11. Показать логи для проверки
echo "📋 Recent logs from search worker:"
ssh $SERVER_USER@$SERVER_IP "docker logs --tail=10 yandex-maps-bot-celery_yandex_search-1"

# 12. Запустить первоначальный мониторинг
echo "📊 Initial performance check:"
ssh $SERVER_USER@$SERVER_IP "$SERVER_PATH/monitor_performance.sh"

echo ""
echo "✅ DEPLOYMENT COMPLETE!"
echo ""
echo "🎯 Expected performance:"
echo "  - Worker 1: 4 concurrent browsers = ~240 clicks/hour"
echo "  - Worker 2: 3 concurrent browsers = ~180 clicks/hour"
echo "  - TOTAL: ~420 clicks/hour * 4 hours = ~1680 clicks/day"
echo "  - With errors/captcha: ~1000-1200 successful clicks/day"
echo ""
echo "🔧 Management commands:"
echo "  - Monitor: ssh root@$SERVER_IP '$SERVER_PATH/monitor_performance.sh'"
echo "  - Cleanup: ssh root@$SERVER_IP '$SERVER_PATH/cleanup_chrome.sh'"
echo "  - Restart: ssh root@$SERVER_IP 'cd $SERVER_PATH && docker-compose restart'"
echo "  - Logs: ssh root@$SERVER_IP 'docker logs -f yandex-maps-bot-celery_yandex_search-1'"
echo ""
echo "📈 Web interface: http://88.99.146.218/yandex-search"
echo "🌸 Flower (Celery monitor): http://88.99.146.218:5555"