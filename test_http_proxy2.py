"""Тест HTTP прокси - попытка 2"""
from tasks.celery_app import app
from tasks.yandex_maps import visit_yandex_maps_profile_task

# Запускаем с явным указанием app
result = visit_yandex_maps_profile_task.apply_async(
    args=[1, 'https://yandex.ru/maps/org/medsemya/1201821372'],
    kwargs={},
)

print(f"✅ Задача запущена: {result.id}")
print(f"   Profile: Profile-1 (id=1)")
print(f"   Прокси: mproxy.site:12138 (HTTP)")
print(f"   URL: https://yandex.ru/maps/org/medsemya/...")

import time
time.sleep(2)

# Проверяем статус
from celery.result import AsyncResult
task_result = AsyncResult(result.id, app=app)
print(f"\n📊 Статус через 2 сек: {task_result.status}")

print("\n📋 Мониторинг:")
print("   python3 -c \"from celery.result import AsyncResult; from tasks.celery_app import app; r=AsyncResult('" + result.id + "', app=app); print(r.status, r.info if not r.successful() else r.result)\"")
print("\n   tail -f logs/celery.log | grep -E '(proxy|Profile-1|Successfully|Error)'")
