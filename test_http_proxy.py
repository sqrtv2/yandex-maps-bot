"""Тест HTTP прокси для Медсемья"""
from tasks.yandex_maps import visit_yandex_maps_profile_task

# Запускаем задачу для Profile-1 с HTTP прокси
result = visit_yandex_maps_profile_task.delay(
    profile_id=1,
    target_url='https://yandex.ru/maps/org/medsemya/1201821372'
)

print(f"✅ Задача запущена: {result.id}")
print(f"   Profile: Profile-1")
print(f"   Прокси: mproxy.site:12138 (HTTP)")
print(f"   URL: https://yandex.ru/maps/org/medsemya/...")
print(f"\nСтатус задачи: {result.status}")
print("\n📋 Проверьте логи через 30-40 секунд:")
print("   tail -100 logs/celery_worker.log")
