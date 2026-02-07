#!/usr/bin/env python3
"""
Check status of Celery Beat scheduler and manual test of scheduler logic.
"""
import sys
sys.path.insert(0, '/Users/sqrtv2/Project/PF')

from datetime import datetime
from app.database import SessionLocal
from app.models.yandex_target import YandexMapTarget
from app.models import BrowserProfile

print("=" * 80)
print("🔍 ПРОВЕРКА ПЛАНИРОВЩИКА ЯНДЕКС КАРТ")
print("=" * 80)
print()

db = SessionLocal()

# Check targets
targets = db.query(YandexMapTarget).filter(YandexMapTarget.is_active == True).all()
print(f"📊 Активных целей: {len(targets)}")
print()

if not targets:
    print("❌ Нет активных целей для посещения!")
    db.close()
    sys.exit(0)

# Check profiles
profiles = db.query(BrowserProfile).filter(
    BrowserProfile.warmup_completed == True
).all()

print(f"✅ Прогретых профилей: {len(profiles)}")
print()

if not profiles:
    print("❌ Нет прогретых профилей!")
    db.close()
    sys.exit(1)

# Test scheduler logic
current_time = datetime.utcnow()

print("=" * 80)
print("📋 АНАЛИЗ ЦЕЛЕЙ")
print("=" * 80)
print()

total_visits_to_schedule = 0

for target in targets:
    print(f"🎯 {target.title}")
    print(f"   ID: {target.id}")
    print(f"   URL: {target.url[:60]}...")
    print(f"   Приоритет: {target.priority}")
    print(f"   Посещений в день: {target.visits_per_day}")
    print(f"   Интервал: {target.min_interval_minutes}-{target.max_interval_minutes} мин")
    print(f"   Одновременных: {target.concurrent_visits}")
    print(f"   Последнее посещение: {target.last_visit_at or 'Никогда'}")
    
    # Test should_visit_now
    should_visit, reason = target.should_visit_now(current_time)
    print(f"   Нужно посетить сейчас: {'✅ ДА' if should_visit else '❌ НЕТ'} - {reason}")
    
    if should_visit:
        visits_needed = target.get_visits_needed_now(current_time)
        print(f"   Посещений запланировать: {visits_needed}")
        total_visits_to_schedule += visits_needed
        
        # Show enabled actions
        actions = []
        for action in ['scroll', 'photos', 'reviews', 'contacts', 'map']:
            if target.is_action_enabled(action):
                actions.append(action)
        print(f"   Включённые действия: {', '.join(actions)}")
    
    print()

print("=" * 80)
print(f"📅 ИТОГО К ЗАПУСКУ: {total_visits_to_schedule} посещений")
print("=" * 80)
print()

if total_visits_to_schedule > 0:
    print("✅ Планировщик должен запустить посещения!")
    print()
    print("🔔 Celery Beat будет проверять каждые 5 минут")
    print("   Задача: tasks.yandex_maps.schedule_visits")
    print()
    print("📝 Для ручного запуска:")
    print("   python3 test_visit_medsemya.py")
    print()
    print("🔍 Проверка Celery Beat:")
    print("   ps aux | grep 'celery.*beat'")
else:
    print("ℹ️  Сейчас нет целей для посещения")
    print("   Причины:")
    print("   • Все цели недавно посещены (ждём минимального интервала)")
    print("   • Нет активных целей")

db.close()
