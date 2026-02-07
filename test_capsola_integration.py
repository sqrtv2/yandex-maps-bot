#!/usr/bin/env python3
"""
Полный тест системы: proxy + SmartCaptcha detection + Capsola solver
"""
import sys
import time
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from app.database import SessionLocal
from app.models.browser_profile import BrowserProfile
from app.models.yandex_target import YandexMapTarget
from tasks.celery_app import celery_app
from tasks.yandex_maps import visit_yandex_maps_profile_task
from celery.result import AsyncResult

def main():
    print("=" * 80)
    print("🧪 ПОЛНЫЙ ТЕСТ: PROXY + SMARTCAPTCHA + CAPSOLA")
    print("=" * 80)
    
    db = SessionLocal()
    try:
        # Get Profile-1
        profile = db.query(BrowserProfile).filter(
            BrowserProfile.name == "Profile-1"
        ).first()
        
        if not profile:
            print("❌ Profile-1 не найден!")
            return
        
        print(f"\n✅ Профиль: {profile.name}")
        print(f"   Прокси: {profile.proxy_type}://{profile.proxy_host}:{profile.proxy_port}")
        print(f"   Логин: {profile.proxy_username}")
        
        # Get target
        target = db.query(YandexMapTarget).filter(
            YandexMapTarget.organization_name == "Медсемья"
        ).first()
        
        if not target:
            print("❌ Медсемья не найдена!")
            return
        
        print(f"\n✅ Цель: {target.organization_name}")
        print(f"   URL: {target.url}")
        
        # Run task
        print("\n" + "=" * 80)
        print("🚀 ЗАПУСКАЕМ ВИЗИТ С КАПЧЕЙ")
        print("=" * 80)
        
        result = visit_yandex_maps_profile_task.apply_async(
            args=[profile.id, target.url],
            queue='yandex'
        )
        
        task_id = result.task_id
        print(f"\n📋 Task ID: {task_id}")
        print("\n⏳ Ожидаем результат (макс 180 сек)...")
        print("   (браузер откроет Яндекс, обнаружит капчу, пошлёт в Capsola)")
        
        # Мониторинг
        for i in range(36):  # 36 * 5 = 180 секунд
            time.sleep(5)
            
            async_result = AsyncResult(task_id, app=celery_app)
            state = async_result.state
            
            print(f"\r⏱️  {(i+1)*5} сек | Status: {state}", end="", flush=True)
            
            if state in ['SUCCESS', 'FAILURE']:
                print()  # Новая строка
                break
                
            if i % 3 == 0:  # Каждые 15 сек показываем прогресс
                if async_result.info:
                    print(f" | Info: {async_result.info}", end="")
        
        print("\n\n" + "=" * 80)
        print("📊 РЕЗУЛЬТАТ")
        print("=" * 80)
        
        async_result = AsyncResult(task_id, app=celery_app)
        
        print(f"Status: {async_result.state}")
        
        if async_result.state == 'SUCCESS':
            print("\n🎉 УСПЕХ!")
            print(f"Result: {async_result.result}")
        elif async_result.state == 'FAILURE':
            print("\n❌ ОШИБКА!")
            print(f"Error: {async_result.info}")
            if async_result.traceback:
                print("\nTraceback:")
                print(async_result.traceback)
        else:
            print(f"\n⚠️  Статус: {async_result.state}")
            print(f"Info: {async_result.info}")
        
        # Проверяем скриншоты
        screenshots_dir = project_root / "screenshots"
        if screenshots_dir.exists():
            recent_screenshots = sorted(
                screenshots_dir.glob("*.png"),
                key=lambda x: x.stat().st_mtime,
                reverse=True
            )[:3]
            
            if recent_screenshots:
                print("\n📸 Последние скриншоты:")
                for sc in recent_screenshots:
                    print(f"   - {sc.name}")
        
        print("\n" + "=" * 80)
        print("✅ ТЕСТ ЗАВЕРШЁН")
        print("=" * 80)
        
    finally:
        db.close()

if __name__ == "__main__":
    main()
