"""Детальный тест прокси с подробными логами"""
from tasks.yandex_maps import visit_yandex_maps_profile_task
import time

print("="*70)
print("🔍 ДЕТАЛЬНЫЙ ТЕСТ ПРОКСИ")
print("="*70)
print("")
print("Профиль: Profile-1")
print("Прокси: mproxy.site:12138 (HTTP)")
print("Логин: Hes9yF")
print("URL: https://yandex.ru/maps/org/medsemya/1201821372")
print("")
print("="*70)

result = visit_yandex_maps_profile_task.apply_async(
    args=[1, 'https://yandex.ru/maps/org/medsemya/1201821372'],
    kwargs={},
)

print(f"✅ Задача запущена: {result.id}")
print("")
print("📋 Ждём 40 секунд...")
print("")

time.sleep(40)

# Проверяем результат
from celery.result import AsyncResult
from tasks.celery_app import app

task_result = AsyncResult(result.id, app=app)

print("="*70)
print("📊 РЕЗУЛЬТАТ:")
print("="*70)
print(f"Статус: {task_result.status}")
if task_result.info:
    print(f"Info: {task_result.info}")
if task_result.traceback:
    print("\nTraceback:")
    print(task_result.traceback[-500:] if len(task_result.traceback) > 500 else task_result.traceback)

print("")
print("="*70)
print("📄 ПОСЛЕДНИЕ ЛОГИ:")
print("="*70)
print("")

import subprocess
logs = subprocess.run(
    ['tail', '-100', 'logs/celery.log'],
    capture_output=True,
    text=True,
    cwd='/Users/sqrtv2/Project/PF'
)

# Фильтруем интересные строки
for line in logs.stdout.split('\n'):
    if any(keyword in line for keyword in ['proxy', 'Proxy', 'PROXY', 'ERR_', 'Using', 'Configuring', result.id[:8]]):
        print(line)
