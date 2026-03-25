#!/usr/bin/env python3
"""
Скрипт настройки системы для достижения 1000 кликов в день.

Настраивает:
1. Оптимальное количество профилей
2. Настройки прокси для стабильности
3. Таргеты для поисковых задач
4. Мониторинг производительности
"""

import requests
import time
import random
import json

# Настройки сервера
API_BASE = "http://88.99.146.218"
AUTH = ("admin", "admin123")

def setup_profiles():
    """Создать оптимальное количество профилей для 1000 кликов/день."""
    print("🔧 Setting up browser profiles...")

    # Для 7 concurrent browsers нужно ~15-20 профилей для ротации
    profiles_needed = 20

    # Проверить текущее количество профилей
    try:
        response = requests.get(f"{API_BASE}/api/profiles", auth=AUTH, timeout=10)
        current_profiles = len(response.json().get('profiles', []))
        print(f"Current profiles: {current_profiles}")

        if current_profiles >= profiles_needed:
            print(f"✅ Already have {current_profiles} profiles (need {profiles_needed})")
            return

        # Создать недостающие профили
        profiles_to_create = profiles_needed - current_profiles
        print(f"Creating {profiles_to_create} new profiles...")

        create_response = requests.post(
            f"{API_BASE}/api/profiles-bulk-create",
            auth=AUTH,
            json={
                "count": profiles_to_create,
                "name_prefix": "OptProfile-",
                "randomize_all": True,
                "auto_start_warmup": True,
                "mobile_percentage": 20,  # 20% mobile profiles
                "config": {
                    "user_agent_type": "generate",
                    "timezone": "random",
                    "language": "random",
                    "viewport_width": "random",
                    "viewport_height": "random"
                }
            },
            timeout=30
        )

        if create_response.status_code == 200:
            result = create_response.json()
            print(f"✅ Created {result['created_count']} profiles")
            print(f"Desktop: {result['desktop_count']}, Mobile: {result['mobile_count']}")
        else:
            print(f"❌ Failed to create profiles: {create_response.text}")

    except Exception as e:
        print(f"❌ Error setting up profiles: {e}")

def setup_search_targets():
    """Настроить поисковые таргеты для тестирования."""
    print("🎯 Setting up search targets...")

    # Примеры поисковых таргетов для тестирования
    test_targets = [
        {
            "domain": "ozon.ru",
            "keywords": ["купить онлайн", "интернет магазин", "доставка товаров"],
            "is_active": True
        },
        {
            "domain": "wildberries.ru",
            "keywords": ["одежда купить", "товары для дома", "электроника"],
            "is_active": True
        },
        {
            "domain": "avito.ru",
            "keywords": ["купить б/у", "объявления", "продать вещи"],
            "is_active": True
        },
        {
            "domain": "market.yandex.ru",
            "keywords": ["сравнить цены", "купить дешево", "отзывы покупателей"],
            "is_active": True
        }
    ]

    try:
        # Получить существующие таргеты
        response = requests.get(f"{API_BASE}/api/yandex-search-targets", auth=AUTH, timeout=10)
        existing_targets = response.json() if response.status_code == 200 else []
        existing_domains = [t.get('domain') for t in existing_targets]

        created_count = 0
        for target in test_targets:
            if target['domain'] not in existing_domains:
                create_response = requests.post(
                    f"{API_BASE}/api/yandex-search-targets",
                    auth=AUTH,
                    json=target,
                    timeout=10
                )

                if create_response.status_code == 200:
                    created_count += 1
                    print(f"✅ Created target: {target['domain']}")
                else:
                    print(f"❌ Failed to create target {target['domain']}: {create_response.text}")
            else:
                print(f"⏭️  Target {target['domain']} already exists")

        print(f"✅ Search targets setup complete. Created: {created_count}")

    except Exception as e:
        print(f"❌ Error setting up search targets: {e}")

def check_system_status():
    """Проверить статус системы."""
    print("📊 Checking system status...")

    try:
        # Проверить здоровье API
        health_response = requests.get(f"{API_BASE}/health", timeout=10)
        if health_response.status_code == 200:
            print("✅ API is healthy")
        else:
            print("❌ API health check failed")

        # Статистика профилей
        profiles_response = requests.get(f"{API_BASE}/api/profiles-overall-progress", auth=AUTH, timeout=10)
        if profiles_response.status_code == 200:
            stats = profiles_response.json()
            print(f"📈 Profiles: {stats['total_profiles']} total, {stats['warmed_profiles']} warmed, {stats['warming_profiles']} warming")

        # Статистика поиска
        search_stats_response = requests.get(f"{API_BASE}/api/yandex-search-stats", auth=AUTH, timeout=10)
        if search_stats_response.status_code == 200:
            search_stats = search_stats_response.json()
            total_today = search_stats.get('total_today', 0)
            success_today = search_stats.get('successful_today', 0)
            print(f"🔍 Today's performance: {success_today}/{total_today} successful clicks")

    except Exception as e:
        print(f"❌ Error checking system status: {e}")

def monitor_performance(duration_minutes: int = 10):
    """Мониторинг производительности в реальном времени."""
    print(f"📊 Monitoring performance for {duration_minutes} minutes...")

    start_time = time.time()
    last_successful = 0

    while time.time() - start_time < duration_minutes * 60:
        try:
            # Получить текущую статистику
            response = requests.get(f"{API_BASE}/api/yandex-search-stats", auth=AUTH, timeout=5)
            if response.status_code == 200:
                stats = response.json()
                successful_today = stats.get('successful_today', 0)
                total_today = stats.get('total_today', 0)

                new_clicks = successful_today - last_successful
                last_successful = successful_today

                current_time = time.strftime("%H:%M:%S")
                success_rate = (successful_today / max(total_today, 1)) * 100

                print(f"[{current_time}] 📈 Total: {successful_today} clicks, New: +{new_clicks}, Success rate: {success_rate:.1f}%")

                # Расчет прогнозируемой производительности на день
                elapsed_hours = (time.time() - start_time) / 3600
                if elapsed_hours > 0:
                    clicks_per_hour = successful_today / elapsed_hours
                    daily_projection = clicks_per_hour * 24
                    print(f"    💡 Projected daily performance: {daily_projection:.0f} clicks/day")

        except Exception as e:
            print(f"❌ Monitoring error: {e}")

        time.sleep(30)  # Обновление каждые 30 секунд

def optimize_settings():
    """Оптимизировать настройки системы для производительности."""
    print("⚙️ Optimizing system settings...")

    optimized_settings = {
        "yandex_search_min_delay": 8,      # Минимум 8 сек между запросами
        "yandex_search_max_delay": 15,     # Максимум 15 сек между запросами
        "browser_timeout": 60,             # 60 сек таймаут браузера
        "captcha_timeout": 180,            # 3 минуты на решение капчи
        "proxy_timeout": 30,               # 30 сек таймаут прокси
        "max_retries": 2,                  # Максимум 2 повтора
        "fast_mode": True,                 # Включить быстрый режим
        "save_screenshots": False,         # Выключить скриншоты для скорости
    }

    for setting_key, value in optimized_settings.items():
        try:
            response = requests.put(
                f"{API_BASE}/api/settings/{setting_key}",
                auth=AUTH,
                json={"value": value},
                timeout=10
            )

            if response.status_code == 200:
                print(f"✅ {setting_key} = {value}")
            else:
                print(f"⚠️ Failed to set {setting_key}: {response.text}")

        except Exception as e:
            print(f"❌ Error setting {setting_key}: {e}")

def main():
    print("🚀 Setting up system for 1000 clicks/day performance")
    print("=" * 60)

    # 1. Проверить текущий статус
    check_system_status()
    print()

    # 2. Настроить профили
    setup_profiles()
    print()

    # 3. Настроить поисковые таргеты
    setup_search_targets()
    print()

    # 4. Оптимизировать настройки
    optimize_settings()
    print()

    # 5. Финальная проверка
    print("⏳ Waiting for profiles to warm up...")
    time.sleep(30)

    check_system_status()
    print()

    print("✅ Setup complete!")
    print()
    print("🎯 Expected performance:")
    print("  - 2 workers with 7 total concurrent browsers")
    print("  - ~60 clicks per hour per browser")
    print("  - ~420 clicks/hour * 4 active hours = ~1680 clicks/day")
    print("  - With captcha/errors: ~1000-1200 successful clicks/day")
    print()
    print("📊 Start monitoring:")
    print("  python3 setup_for_1000_clicks.py --monitor")
    print()
    print("🔗 Web interface: http://88.99.146.218/yandex-search")

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--monitor":
        duration = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        monitor_performance(duration)
    else:
        main()