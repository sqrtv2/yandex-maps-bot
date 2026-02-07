#!/usr/bin/env python3
"""
Test script to manually trigger Yandex Maps visit for Медсемья target.
"""
import sys
sys.path.insert(0, '/Users/sqrtv2/Project/PF')

from app.database import SessionLocal
from app.models.yandex_target import YandexMapTarget
from app.models import BrowserProfile
from tasks.yandex_scheduler import force_visit_target

print("=" * 80)
print("🚀 ТЕСТОВЫЙ ЗАПУСК ПОСЕЩЕНИЯ ЯНДЕКС КАРТ")
print("=" * 80)
print()

# Check database
db = SessionLocal()

# Find Медсемья target
target = db.query(YandexMapTarget).filter(YandexMapTarget.title == "Медсемья").first()

if not target:
    print("❌ Цель 'Медсемья' не найдена в базе данных!")
    db.close()
    sys.exit(1)

print(f"✅ Найдена цель: {target.title}")
print(f"   URL: {target.url}")
print(f"   Активна: {'🟢 Да' if target.is_active else '🔴 Нет'}")
print(f"   Посещений в день: {target.visits_per_day}")
print(f"   Интервал: {target.min_interval_minutes}-{target.max_interval_minutes} мин")
print(f"   Длительность: {target.min_visit_duration}-{target.max_visit_duration} сек")
print()

# Check warmed profiles
profiles = db.query(BrowserProfile).filter(
    BrowserProfile.warmup_completed == True
).all()

if not profiles:
    print("❌ Нет прогретых профилей!")
    print("   Запустите прогрев профилей:")
    print("   python3 warmup_profiles.py")
    db.close()
    sys.exit(1)

print(f"✅ Найдено прогретых профилей: {len(profiles)}")
for p in profiles[:3]:
    print(f"   • Профиль {p.id}: {p.name} (последнее использование: {p.last_used_at})")
print()

db.close()

# Ask for confirmation
print("🔔 Готов запустить посещение:")
print(f"   Цель: {target.title}")
print(f"   Профиль: {profiles[0].id} ({profiles[0].name})")
print()

response = input("Запустить? (yes/no): ").strip().lower()

if response in ['yes', 'y', 'да', 'д']:
    print()
    print("🚀 Запускаю посещение...")
    
    # Force visit using the scheduler
    result = force_visit_target(target.id, profiles[0].id)
    
    print()
    print("=" * 80)
    print("📊 РЕЗУЛЬТАТ:")
    print("=" * 80)
    if result['status'] == 'success':
        print(f"✅ Задача успешно запущена!")
        print(f"   Task ID: {result.get('task_id')}")
        print()
        print("📝 Мониторинг:")
        print("   • Логи Celery: tail -f logs/celery.log")
        print("   • Веб-интерфейс: http://127.0.0.1:8000/tasks")
        print()
        print("⏱️  Визит займёт примерно 2-10 минут")
    else:
        print(f"❌ Ошибка: {result.get('error', result.get('message'))}")
    print("=" * 80)
else:
    print()
    print("❌ Отменено")
