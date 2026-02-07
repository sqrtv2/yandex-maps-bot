#!/usr/bin/env python3
"""
Quick test to launch visit immediately.
"""
import sys
import os

# Change to project directory
os.chdir('/Users/sqrtv2/Project/PF')
sys.path.insert(0, '/Users/sqrtv2/Project/PF')

print("=" * 80)
print("🚀 ПРИНУДИТЕЛЬНЫЙ ЗАПУСК ПОСЕЩЕНИЯ МЕДСЕМЬЯ")
print("=" * 80)
print()

from tasks.yandex_scheduler import force_visit_target

result = force_visit_target(target_id=1, profile_id=1)

print(f"Статус: {result.get('status')}")
print(f"Цель: {result.get('target', 'N/A')}")
print(f"Профиль: {result.get('profile_id', 'N/A')}")
print(f"Task ID: {result.get('task_id', 'N/A')}")

if result.get('error'):
    print(f"❌ Ошибка: {result.get('error')}")
else:
    print()
    print("✅ Задача отправлена в Celery!")
    print()
    print("📝 Мониторинг:")
    print("   tail -f logs/celery.log")
    print()
    print("⏱️  Процесс займёт 2-10 минут")

print()
print("=" * 80)
