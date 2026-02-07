#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Проверка текущих целей и вывод в формате таблицы
"""
from app.database import SessionLocal
from app.models import YandexMapTarget

with SessionLocal() as db:
    targets = db.query(YandexMapTarget).order_by(YandexMapTarget.priority.desc()).all()
    
    print("\n" + "=" * 120)
    print("📊 ЦЕЛИ ЯНДЕКС КАРТ - ТЕКУЩЕЕ СОСТОЯНИЕ")
    print("=" * 120)
    print()
    
    if not targets:
        print("❌ Нет целей в базе данных")
        print("\n💡 Запустите: python3 add_test_targets.py")
    else:
        # Статистика
        total = len(targets)
        active = sum(1 for t in targets if t.is_active)
        inactive = total - active
        total_visits_per_day = sum(t.visits_per_day for t in targets if t.is_active)
        
        print(f"📈 Статистика:")
        print(f"   Всего целей: {total}")
        print(f"   Активных: {active} 🟢")
        print(f"   Неактивных: {inactive} 🔴")
        print(f"   Посещений в день (планируется): {total_visits_per_day}")
        print()
        print("-" * 120)
        
        # Заголовок таблицы
        print(f"{'ID':<4} {'Статус':<10} {'Название':<30} {'Посещ/день':<12} {'Интервал (мин)':<16} {'Потоков':<8} {'Приоритет':<10}")
        print("-" * 120)
        
        # Данные
        for t in targets:
            status = "🟢 Активна" if t.is_active else "🔴 Неактивна"
            title = t.title[:28] + ".." if len(t.title) > 30 else t.title
            interval = f"{t.min_interval_minutes}-{t.max_interval_minutes}"
            
            print(f"{t.id:<4} {status:<10} {title:<30} {t.visits_per_day:<12} {interval:<16} {t.concurrent_visits:<8} {'⭐' * t.priority:<10}")
        
        print("-" * 120)
        print()
        
        # Детали по каждой цели
        print("📋 Подробная информация:")
        print()
        for t in targets:
            status_emoji = "🟢" if t.is_active else "🔴"
            print(f"{status_emoji} [{t.id}] {t.title}")
            print(f"   URL: {t.url}")
            print(f"   📊 Посещений: {t.visits_per_day}/день")
            print(f"   ⏱️  Интервал: {t.min_interval_minutes}-{t.max_interval_minutes} минут")
            print(f"   ⏰ На странице: {t.min_visit_duration}-{t.max_visit_duration} секунд")
            print(f"   🔢 Потоков одновременно: {t.concurrent_visits}")
            print(f"   ⭐ Приоритет: {t.priority}/10")
            print(f"   🎬 Действия: {t.enabled_actions}")
            if t.notes:
                print(f"   💭 Заметки: {t.notes}")
            print(f"   📈 Статистика: {t.successful_visits}/{t.total_visits} успешно")
            print()
    
    print("=" * 120)
    print()
    print("🌐 Откройте в браузере для управления:")
    print("   http://127.0.0.1:8000/yandex-targets")
    print()
    print("🎯 Доступные действия в веб-интерфейсе:")
    print("   ✏️  Редактировать - изменить любые настройки")
    print("   ▶️  Запустить - активировать автоматические посещения")
    print("   ⏸️  Остановить - приостановить посещения")
    print("   🗑️  Удалить - удалить цель из системы")
    print("   ➕ Добавить URL - создать новую цель")
    print()
