"""Финальный тест HTTP прокси через Celery с selenium-wire"""
from tasks.yandex_maps import visit_yandex_maps_profile_task
import time

print("="*80)
print("🎯 ФИНАЛЬНЫЙ ТЕСТ: HTTP ПРОКСИ С SELENIUM-WIRE")
print("="*80)
print("")
print("✅ Изменения:")
print("   - Установлен selenium-wire")
print("   - Browser_manager использует selenium-wire для прокси с авторизацией")
print("   - HTTP прокси: mproxy.site:12138")
print("   - Авторизация: Hes9yF / zAU2vaEUf4TU")
print("")
print("="*80)

result = visit_yandex_maps_profile_task.apply_async(
    args=[1, 'https://yandex.ru/maps/org/medsemya/1201821372'],
    kwargs={},
)

print(f"\n✅ Задача запущена: {result.id}")
print(f"   Profile: Profile-1")
print(f"   URL: https://yandex.ru/maps/org/medsemya/...")
print("")
print("⏳ Ждём 45 секунд...")

time.sleep(45)

# Проверяем результат
from celery.result import AsyncResult
from tasks.celery_app import app

task_result = AsyncResult(result.id, app=app)

print("")
print("="*80)
print("📊 РЕЗУЛЬТАТ:")
print("="*80)
print(f"Статус: {task_result.status}")

if task_result.info:
    info_str = str(task_result.info)[:200]
    print(f"Info: {info_str}")

if task_result.status == 'SUCCESS':
    print("\n🎉 ПРОКСИ РАБОТАЕТ!")
elif task_result.status == 'RETRY':
    print("\n⚠️ Задача в retry (возможно из-за Яндекс защиты)")
elif task_result.status == 'FAILURE':
    print("\n❌ Задача провалилась")
    if task_result.traceback:
        print("Последние 300 символов traceback:")
        print(task_result.traceback[-300:])

print("")
print("="*80)
print("📄 ПОСЛЕДНИЕ ЛОГИ:")
print("="*80)

import subprocess
logs = subprocess.run(
    ['tail', '-150', 'logs/celery.log'],
    capture_output=True,
    text=True,
    cwd='/Users/sqrtv2/Project/PF'
)

# Фильтруем интересные строки
for line in logs.stdout.split('\n'):
    if any(keyword in line.lower() for keyword in ['selenium-wire', 'proxy', result.id[:10], 'using', 'successfully', 'error', 'err_']):
        print(line)

print("")
print("="*80)
