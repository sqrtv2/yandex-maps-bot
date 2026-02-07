#!/bin/bash
# Быстрая проверка веб-интерфейса Яндекс Карт

echo "🔍 Проверка системы..."
echo ""

# Проверка FastAPI
if pgrep -f "uvicorn app.main:app" > /dev/null; then
    echo "✅ FastAPI запущен на http://127.0.0.1:8000"
else
    echo "❌ FastAPI не запущен!"
    echo "   Запустите: python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"
    exit 1
fi

# Проверка API endpoint
echo ""
echo "🔌 Проверка API endpoint..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/api/yandex-targets)
if [ "$HTTP_CODE" = "200" ]; then
    echo "✅ API endpoint работает (HTTP $HTTP_CODE)"
else
    echo "❌ API endpoint не работает (HTTP $HTTP_CODE)"
    exit 1
fi

# Проверка веб-страницы
echo ""
echo "🌐 Проверка веб-страницы..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/yandex-targets)
if [ "$HTTP_CODE" = "200" ]; then
    echo "✅ Веб-страница доступна (HTTP $HTTP_CODE)"
else
    echo "❌ Веб-страница недоступна (HTTP $HTTP_CODE)"
    exit 1
fi

# Показываем текущие цели
echo ""
echo "📊 Текущие цели в базе данных:"
python3 -c "
from app.database import SessionLocal
from app.models import YandexMapTarget

with SessionLocal() as db:
    targets = db.query(YandexMapTarget).all()
    if targets:
        for t in targets:
            status = '🟢' if t.is_active else '🔴'
            print(f'   {status} [{t.id}] {t.title} - {t.visits_per_day} посещений/день')
    else:
        print('   ⚠️  Нет целей в базе')
        print('   Запустите: python3 add_test_targets.py')
"

echo ""
echo "=" * 70
echo "✅ ВСЁ ГОТОВО К ТЕСТИРОВАНИЮ!"
echo "=" * 70
echo ""
echo "🌐 Откройте в браузере:"
echo "   http://127.0.0.1:8000/yandex-targets"
echo ""
echo "🎯 Что делать:"
echo "   1. Посмотрите таблицу с целями"
echo "   2. Попробуйте кнопки: ✏️ Редактировать, ▶️ Запустить, 🗑️ Удалить"
echo "   3. Нажмите 'Добавить URL' для создания новой цели"
echo ""
