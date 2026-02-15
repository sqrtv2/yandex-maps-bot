#!/bin/bash
# Quick status check script

echo "📊 СТАТУС СИСТЕМЫ"
echo "================="
echo ""

echo "🌐 API Server:"
curl -s http://127.0.0.1:8000/health 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "❌ Не отвечает"
echo ""

echo "⚙️ Celery Workers:"
ps aux | grep "celery.*worker" | grep -v grep | wc -l | xargs echo "Процессов:"
echo ""

echo "🌍 Браузеры Chrome:"
ps aux | grep "Google Chrome" | grep -v "grep\|Helper" | wc -l | xargs echo "Запущено:"
echo ""

echo "📈 Прогресс профилей:"
python3 -c "
from app.database import get_db_session
from app.models import BrowserProfile

with get_db_session() as db:
    profiles = db.query(BrowserProfile).all()
    warming = sum(1 for p in profiles if p.status == 'warming_up')
    warmed = sum(1 for p in profiles if p.warmup_completed)
    error = sum(1 for p in profiles if p.status == 'error')
    total = len(profiles)
    
    print(f'  ✅ Прогрето: {warmed}/{total}')
    print(f'  ⏳ В процессе: {warming}')
    print(f'  ❌ Ошибки: {error}')
    print(f'  📊 Прогресс: {int(warmed/total*100)}%')
    print()
    for p in profiles:
        status = '✅' if p.warmup_completed else '⏳' if p.status == 'warming_up' else '❌'
        print(f'  {status} {p.name}: {p.status}')
"
