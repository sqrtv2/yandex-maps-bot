#!/usr/bin/env python3
"""
Final test - schedule visit for Medsemya now.
"""
import sys
import os

os.chdir('/Users/sqrtv2/Project/PF')
sys.path.insert(0, '/Users/sqrtv2/Project/PF')

print("=" * 80)
print("🎯 ФИНАЛЬНЫЙ ТЕСТ: ПОСЕЩЕНИЕ МЕДСЕМЬЯ")
print("=" * 80)
print()

# Step 1: Check profile readiness
from app.database import SessionLocal
from app.models import BrowserProfile
from app.models.yandex_target import YandexMapTarget

db = SessionLocal()

profile = db.query(BrowserProfile).first()
target = db.query(YandexMapTarget).filter(YandexMapTarget.title == "Медсемья").first()

print(f"1️⃣ Проверка профиля:")
print(f"   ID: {profile.id}")
print(f"   Имя: {profile.name}")
print(f"   Готов к задачам: {'✅ ДА' if profile.is_ready_for_tasks() else '❌ НЕТ'}")
print()

print(f"2️⃣ Проверка цели:")
print(f"   Название: {target.title}")
print(f"   URL: {target.url[:60]}...")
print(f"   Активна: {'✅ ДА' if target.is_active else '❌ НЕТ'}")
print()

# Reset last_visit_at for immediate scheduling
target.last_visit_at = None
db.commit()
print(f"3️⃣ Сброшен last_visit_at для немедленного запуска")
print()

# Step 2: Schedule visit
print(f"4️⃣ Отправка задачи в Celery...")
from tasks.yandex_maps import visit_yandex_maps_profile_task

# Store IDs before closing session
profile_id = profile.id
target_url = target.url

db.close()

visit_params = {
    'min_visit_time': 120,
    'max_visit_time': 180,
    'actions': ['scroll', 'view_photos', 'read_reviews'],
    'scroll_probability': 0.9,
    'photo_click_probability': 0.7,
    'review_read_probability': 0.8,
}

result = visit_yandex_maps_profile_task.apply_async(
    args=[profile_id, target_url, visit_params],
    queue='yandex'
)

print(f"✅ Задача отправлена!")
print(f"   Task ID: {result.id}")
print(f"   Состояние: {result.state}")
print()

print("=" * 80)
print("📊 МОНИТОРИНГ:")
print("=" * 80)
print()
print("Посещение займёт 2-5 минут. Следите за логами:")
print()
print("   tail -f logs/celery.log")
print()
print("Что вы должны увидеть:")
print("   1. 'Starting Yandex Maps visit for profile ...'")
print("   2. 'Initializing browser with profile ...'")
print("   3. 'Navigating to target URL...'")
print("   4. Выполнение действий (scroll, photos, reviews)")
print("   5. 'Visit completed successfully'")
print()
print("=" * 80)
