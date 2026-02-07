#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт для добавления тестовых целей Яндекс Карт
"""
from app.database import SessionLocal
from app.models import YandexMapTarget

# Удаляем старые тестовые данные
with SessionLocal() as db:
    db.query(YandexMapTarget).delete()
    db.commit()
    print("🗑️  Старые цели удалены")

# Тестовые цели для добавления
test_targets = [
    {
        "url": "https://yandex.ru/maps/org/kofeynya_starbucks/1234567890",
        "title": "Кофейня Starbucks",
        "organization_name": "Starbucks на Тверской",
        "visits_per_day": 15,
        "min_interval_minutes": 45,
        "max_interval_minutes": 120,
        "min_visit_duration": 90,
        "max_visit_duration": 300,
        "concurrent_visits": 2,
        "priority": 8,
        "notes": "Популярная кофейня в центре"
    },
    {
        "url": "https://yandex.ru/maps/org/restoran_pushkin/9876543210",
        "title": "Ресторан Пушкинъ",
        "organization_name": "Ресторан Пушкинъ",
        "visits_per_day": 20,
        "min_interval_minutes": 30,
        "max_interval_minutes": 90,
        "min_visit_duration": 120,
        "max_visit_duration": 600,
        "concurrent_visits": 3,
        "priority": 10,
        "notes": "Премиум ресторан - максимальный приоритет"
    },
    {
        "url": "https://yandex.ru/maps/org/fitness_klub_world_class/5555555555",
        "title": "Фитнес-клуб World Class",
        "organization_name": "World Class",
        "visits_per_day": 8,
        "min_interval_minutes": 90,
        "max_interval_minutes": 240,
        "min_visit_duration": 60,
        "max_visit_duration": 180,
        "concurrent_visits": 1,
        "priority": 5,
        "notes": "Средний приоритет"
    },
    {
        "url": "https://yandex.ru/maps/org/magazin_pyaterochka/1111111111",
        "title": "Магазин Пятёрочка",
        "organization_name": "Пятёрочка на Ленина",
        "visits_per_day": 5,
        "min_interval_minutes": 120,
        "max_interval_minutes": 300,
        "min_visit_duration": 45,
        "max_visit_duration": 120,
        "concurrent_visits": 1,
        "priority": 3,
        "is_active": False,
        "notes": "Неактивная цель - для тестирования"
    }
]

print("\n📦 Добавление тестовых целей...\n")

with SessionLocal() as db:
    for i, target_data in enumerate(test_targets, 1):
        target = YandexMapTarget(**target_data)
        db.add(target)
        db.commit()
        db.refresh(target)
        
        status = "🟢 Активна" if target.is_active else "🔴 Неактивна"
        print(f"{i}. {status} [{target.id}] {target.title}")
        print(f"   📍 {target.url[:70]}...")
        print(f"   📊 {target.visits_per_day} посещений/день")
        print(f"   ⏱️  Интервал: {target.min_interval_minutes}-{target.max_interval_minutes} мин")
        print(f"   🔢 Потоков: {target.concurrent_visits}")
        print(f"   ⭐ Приоритет: {target.priority}/10")
        if target.notes:
            print(f"   💭 {target.notes}")
        print()

print("=" * 70)
print("✅ Тестовые данные добавлены!")
print("=" * 70)
print()
print("🌐 Откройте в браузере:")
print("   http://127.0.0.1:8000/yandex-targets")
print()
print("👀 Что вы увидите:")
print("   • 4 организации в таблице")
print("   • 3 активных цели (зелёные)")
print("   • 1 неактивная цель (серая)")
print("   • Статистику по каждой цели")
print("   • Кнопки управления (редактировать, вкл/выкл, удалить)")
print()
print("🎯 Попробуйте:")
print("   1. Нажмите на кнопку ▶️ (play) у неактивной цели")
print("   2. Нажмите ✏️ (карандаш) для редактирования")
print("   3. Нажмите 'Добавить URL' для создания своей цели")
print("   4. Нажмите 🗑️ (корзина) для удаления")
print()
