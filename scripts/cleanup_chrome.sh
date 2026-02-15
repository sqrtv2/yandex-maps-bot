#!/bin/bash
# Script to cleanup orphaned Chrome/ChromeDriver processes

echo "🧹 Очистка зависших процессов Chrome/ChromeDriver"
echo "================================================"
echo

# Подсчитываем процессы
CHROME_COUNT=$(ps aux | grep -i "Google Chrome.app" | grep -v grep | wc -l | tr -d ' ')
DRIVER_COUNT=$(ps aux | grep -i "chromedriver" | grep -v grep | wc -l | tr -d ' ')

echo "Найдено процессов:"
echo "  Chrome: $CHROME_COUNT"
echo "  ChromeDriver: $DRIVER_COUNT"
echo

if [ "$CHROME_COUNT" -eq 0 ] && [ "$DRIVER_COUNT" -eq 0 ]; then
    echo "✅ Нет процессов для очистки"
    exit 0
fi

# Спрашиваем подтверждение
read -p "❓ Убить все процессы Chrome/ChromeDriver? (y/n): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "❌ Отменено"
    exit 0
fi

echo
echo "🔪 Завершаем процессы..."

# Убиваем ChromeDriver процессы
if [ "$DRIVER_COUNT" -gt 0 ]; then
    echo "  Убиваем ChromeDriver процессы..."
    pkill -f "undetected_chromedriver" 2>/dev/null
    sleep 1
fi

# Убиваем Chrome процессы (только те, что запущены из browser_profiles)
if [ "$CHROME_COUNT" -gt 0 ]; then
    echo "  Убиваем Chrome процессы с профилями..."
    ps aux | grep -i "Google Chrome.app" | grep "browser_profiles" | grep -v grep | awk '{print $2}' | xargs kill -9 2>/dev/null
    sleep 1
fi

# Проверяем результат
CHROME_COUNT_AFTER=$(ps aux | grep -i "Google Chrome.app" | grep -v grep | wc -l | tr -d ' ')
DRIVER_COUNT_AFTER=$(ps aux | grep -i "chromedriver" | grep -v grep | wc -l | tr -d ' ')

echo
echo "✅ Готово!"
echo "Осталось процессов:"
echo "  Chrome: $CHROME_COUNT_AFTER"
echo "  ChromeDriver: $DRIVER_COUNT_AFTER"
