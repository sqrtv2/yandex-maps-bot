#!/usr/bin/env python3
"""
Direct test of yandex_maps task.
"""
import sys
sys.path.insert(0, '/Users/sqrtv2/Project/PF')

print("=" * 80)
print("🧪 ПРЯМОЙ ТЕСТ ЗАДАЧИ ЯНДЕКС КАРТ")
print("=" * 80)
print()

from tasks.yandex_maps import visit_yandex_maps_profile_task

# Test target
target_url = "https://yandex.ru/maps/org/medsemya/108007547689/"
profile_id = 1

visit_params = {
    'min_visit_time': 120,
    'max_visit_time': 180,
    'actions': ['scroll', 'view_photos'],
}

print(f"📋 Параметры:")
print(f"   Profile ID: {profile_id}")
print(f"   URL: {target_url}")
print(f"   Время: {visit_params['min_visit_time']}-{visit_params['max_visit_time']} сек")
print()
print("🚀 Отправка задачи в Celery...")
print()

# Send task
result = visit_yandex_maps_profile_task.apply_async(
    args=[profile_id, target_url, visit_params],
    queue='yandex'
)

print(f"✅ Задача отправлена!")
print(f"   Task ID: {result.id}")
print()
print("📝 Проверка статуса:")
print(f"   result.state: {result.state}")
print()
print("🔍 Мониторинг:")
print("   tail -f logs/celery.log")
print()
print("=" * 80)
