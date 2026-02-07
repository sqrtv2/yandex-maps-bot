#!/usr/bin/env python3
"""
Script to visit Yandex Maps profiles using warmed browser profiles.
"""
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from app.database import SessionLocal
from app.models import BrowserProfile
from tasks.yandex_maps import visit_yandex_maps_profile_task


def visit_yandex_maps(target_url: str, profile_id: int = None):
    """
    Visit Yandex Maps profile with a warmed browser profile.
    
    Args:
        target_url: Yandex Maps profile URL to visit
        profile_id: Specific profile ID to use (optional, will pick random if not provided)
    """
    with SessionLocal() as db:
        # Get available warmed profiles
        if profile_id:
            profile = db.query(BrowserProfile).filter(
                BrowserProfile.id == profile_id,
                BrowserProfile.warmup_completed == True
            ).first()
            
            if not profile:
                print(f"❌ Профиль #{profile_id} не найден или не прогрет")
                return
        else:
            # Pick a random warmed profile
            profile = db.query(BrowserProfile).filter(
                BrowserProfile.warmup_completed == True
            ).first()
            
            if not profile:
                print("❌ Нет доступных прогретых профилей")
                return
        
        print(f"🚀 Запуск задачи посещения Яндекс Карт")
        print(f"   Профиль: {profile.name} (ID: {profile.id})")
        print(f"   URL: {target_url}")
        print()
        
        # Start async task
        task = visit_yandex_maps_profile_task.delay(
            profile_id=profile.id,
            target_url=target_url
        )
        
        print(f"✅ Задача запущена!")
        print(f"   Task ID: {task.id}")
        print(f"   Статус: {task.state}")
        print()
        print("📊 Отслеживайте прогресс:")
        print(f"   - Web UI: http://127.0.0.1:8000/tasks")
        print(f"   - Логи: tail -f logs/celery.log")
        
        return task.id


def visit_multiple_profiles(target_urls: list, use_all_profiles: bool = False):
    """
    Visit multiple Yandex Maps profiles using different browser profiles.
    
    Args:
        target_urls: List of Yandex Maps URLs to visit
        use_all_profiles: If True, use all available profiles (one URL per profile)
    """
    with SessionLocal() as db:
        profiles = db.query(BrowserProfile).filter(
            BrowserProfile.warmup_completed == True
        ).all()
        
        if not profiles:
            print("❌ Нет доступных прогретых профилей")
            return
        
        print(f"🚀 Запуск множественного посещения")
        print(f"   Профилей доступно: {len(profiles)}")
        print(f"   URL для посещения: {len(target_urls)}")
        print()
        
        tasks = []
        for i, url in enumerate(target_urls):
            # Rotate through profiles
            profile = profiles[i % len(profiles)]
            
            task = visit_yandex_maps_profile_task.delay(
                profile_id=profile.id,
                target_url=url
            )
            
            tasks.append({
                'task_id': task.id,
                'profile': profile.name,
                'url': url
            })
            
            print(f"✅ Запущено: {profile.name} → {url[:60]}...")
        
        print()
        print(f"📊 Всего запущено задач: {len(tasks)}")
        print("   Отслеживайте на: http://127.0.0.1:8000/tasks")
        
        return tasks


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Visit Yandex Maps profiles')
    parser.add_argument('url', nargs='?', help='Yandex Maps URL to visit')
    parser.add_argument('--profile', type=int, help='Specific profile ID to use')
    parser.add_argument('--multiple', nargs='+', help='Multiple URLs to visit')
    parser.add_argument('--all-profiles', action='store_true', help='Use all available profiles')
    
    args = parser.parse_args()
    
    if args.multiple:
        # Visit multiple URLs
        visit_multiple_profiles(args.multiple, args.all_profiles)
    elif args.url:
        # Visit single URL
        visit_yandex_maps(args.url, args.profile)
    else:
        # Interactive mode
        print("🗺️  Посещение Яндекс Карт")
        print("=" * 50)
        print()
        
        url = input("Введите URL Яндекс Карт: ").strip()
        
        if not url:
            print("❌ URL не указан")
            sys.exit(1)
        
        use_specific = input("Использовать конкретный профиль? (y/n): ").strip().lower()
        
        if use_specific == 'y':
            profile_id = int(input("Введите ID профиля: ").strip())
            visit_yandex_maps(url, profile_id)
        else:
            visit_yandex_maps(url)
