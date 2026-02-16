#!/usr/bin/env python3
"""
Immediately trigger the scheduler manually (without waiting for beat).
"""
import sys
sys.path.insert(0, '/Users/sqrtv2/Project/PF')

from tasks.yandex_scheduler import schedule_yandex_visits

print("=" * 80)
print("🚀 РУЧНОЙ ЗАПУСК ПЛАНИРОВЩИКА")
print("=" * 80)
print()
print("Запускаю проверку всех активных целей и планирование посещений...")
print()

result = schedule_yandex_visits()

print("=" * 80)
print("📊 РЕЗУЛЬТАТ:")
print("=" * 80)
print()

if result['status'] == 'success':
    print(f"✅ Успешно!")
    print(f"   Обработано целей: {result.get('targets_processed', 0)}")
    print(f"   Запланировано посещений: {result.get('scheduled', 0)}")
    print(f"   Время: {result.get('timestamp', 'N/A')}")
    print()
    
    if result.get('scheduled', 0) > 0:
        print("📝 Задачи отправлены в Celery очередь 'yandex_maps'")
        print()
        print("🔍 Мониторинг:")
        print("   • Логи Celery: tail -f logs/celery.log")
        print("   • Веб-интерфейс: http://127.0.0.1:8000/tasks")
        print()
        print("⏱️  Посещения начнутся в течение нескольких секунд")
    else:
        print("ℹ️  Не было целей для посещения (возможно недавно посещены)")
else:
    print(f"❌ Ошибка: {result.get('error', 'Unknown error')}")

print()
print("=" * 80)
