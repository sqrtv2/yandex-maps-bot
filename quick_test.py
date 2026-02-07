#!/usr/bin/env python3
"""
Быстрый тест - получить task ID и проверить через 90 секунд
"""
import sys
import time
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from app.database import SessionLocal
from app.models.browser_profile import BrowserProfile
from app.models.yandex_target import YandexMapTarget
from tasks.yandex_maps import visit_yandex_maps_profile_task
from celery.result import AsyncResult
from tasks.celery_app import celery_app

def main():
    print("🚀 БЫСТРЫЙ ТЕСТ CAPSOLA")
    
    db = SessionLocal()
    try:
        profile = db.query(BrowserProfile).filter(BrowserProfile.name == "Profile-1").first()
        target = db.query(YandexMapTarget).filter(YandexMapTarget.organization_name == "Медсемья").first()
        
        if not profile or not target:
            print("❌ Данные не найдены")
            return
        
        print(f"\n✅ Профиль: {profile.name} ({profile.proxy_type}://{profile.proxy_host}:{profile.proxy_port})")
        print(f"✅ Цель: {target.organization_name}")
        print(f"   URL: {target.url}\n")
        
        # Запускаем
        result = visit_yandex_maps_profile_task.apply_async(
            args=[profile.id, target.url],
            queue='yandex'
        )
        
        task_id = result.task_id
        print(f"📋 Task ID: {task_id}\n")
        print("⏳ Жду 90 секунд...\n")
        
        # Мониторинг
        for i in range(18):  # 18 * 5 = 90 сек
            time.sleep(5)
            async_result = AsyncResult(task_id, app=celery_app)
            state = async_result.state
            
            status_str = f"[{(i+1)*5}s] {state}"
            if async_result.info:
                info_str = str(async_result.info)[:80]
                status_str += f" - {info_str}"
            
            print(status_str)
            
            if state in ['SUCCESS', 'FAILURE']:
                break
        
        # Финальный результат
        async_result = AsyncResult(task_id, app=celery_app)
        print(f"\n{'='*80}")
        print(f"РЕЗУЛЬТАТ: {async_result.state}")
        print(f"{'='*80}")
        
        if async_result.state == 'SUCCESS':
            print(f"\n✅ УСПЕХ!\n{async_result.result}")
        elif async_result.state == 'FAILURE':
            print(f"\n❌ ОШИБКА!\n{async_result.info}")
        else:
            print(f"\n⚠️  {async_result.state}")
            if async_result.info:
                print(f"{async_result.info}")
        
        # Проверяем скриншоты
        screenshots = sorted((project_root / "screenshots").glob("captcha_*.png"), key=lambda x: x.stat().st_mtime, reverse=True)
        if screenshots:
            print(f"\n📸 Последний скриншот капчи: {screenshots[0].name}")
        
    finally:
        db.close()

if __name__ == "__main__":
    main()
