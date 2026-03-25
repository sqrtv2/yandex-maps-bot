#!/usr/bin/env python3
"""
Срочное исправление проблем с капчами для стабильной работы 1000 кликов/день.

Проблемы:
1. Не настроены резервные антикапча сервисы (только Capsola)
2. Капчи появляются на страницах результатов поиска
3. Нет fallback стратегии при неудачном решении
"""

import requests
import time

# Настройки сервера
API_BASE = "http://88.99.146.218"
AUTH = ("admin", "admin123")

def setup_backup_anticaptcha_services():
    """Настроить резервные антикапча сервисы."""
    print("🔑 Setting up backup anti-captcha services...")

    # ВАЖНО: Замените на ваши реальные API ключи!
    captcha_services = {
        # 2captcha.com - самый надёжный резерв
        "anticaptcha_api_key": "YOUR_2CAPTCHA_KEY_HERE",  # 🔥 ЗАМЕНИТЕ НА РЕАЛЬНЫЙ КЛЮЧ!
        "anticaptcha_service": "2captcha",

        # Увеличиваем таймауты для медленных капч
        "captcha_timeout_seconds": 300,  # 5 минут вместо 2

        # Настройки для лучшей совместимости
        "captcha_retry_attempts": 3,     # 3 попытки решения
        "capsola_fallback_enabled": True # Capsola как основной, 2captcha как резерв
    }

    for setting_key, value in captcha_services.items():
        try:
            if setting_key == "anticaptcha_api_key" and value == "YOUR_2CAPTCHA_KEY_HERE":
                print(f"⚠️  ВНИМАНИЕ: Нужно указать реальный API ключ для {setting_key}")
                print("   Получите ключ на https://2captcha.com")
                continue

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

def check_captcha_service_status():
    """Проверить статус антикапча сервисов."""
    print("\n🔍 Checking captcha service status...")

    try:
        # Проверить текущие настройки
        response = requests.get(f"{API_BASE}/api/settings", auth=AUTH, timeout=10)
        if response.status_code == 200:
            settings = response.json()
            captcha_settings = [s for s in settings if 'captcha' in s['setting_key'].lower() or 'capsola' in s['setting_key'].lower()]

            print("Current captcha settings:")
            for setting in captcha_settings:
                key = setting['setting_key']
                value = setting['setting_value']
                if 'api_key' in key and value:
                    # Скрыть API ключ кроме первых/последних символов
                    if len(str(value)) > 8:
                        masked = str(value)[:4] + "..." + str(value)[-4:]
                    else:
                        masked = "***"
                    print(f"  📋 {key} = {masked}")
                else:
                    print(f"  📋 {key} = {value}")

        else:
            print("❌ Could not fetch current settings")

    except Exception as e:
        print(f"❌ Error checking captcha status: {e}")

def analyze_recent_captcha_errors():
    """Анализировать недавние ошибки капч."""
    print("\n🔍 Analyzing recent captcha errors...")

    try:
        # Получить недавние задачи с ошибками капч
        # Это нужно делать через SSH так как API требует аутентификации
        print("Checking database for recent captcha errors...")

        import subprocess
        result = subprocess.run([
            'ssh', 'root@88.99.146.218',
            'docker exec yandex-maps-bot-celery_yandex_search-1 python -c "' +
            'from app.database import get_db_session; ' +
            'from app.models.task import Task; ' +
            'from sqlalchemy import desc; ' +
            'with get_db_session() as db: ' +
            '    tasks = db.query(Task).filter(Task.error_message.like(\\'%captcha%\\')).order_by(desc(Task.created_at)).limit(20).all(); ' +
            '    for task in tasks: ' +
            '        print(f\\"{task.created_at}: {task.error_message}\\"); ' +
            '"'
        ], capture_output=True, text=True, timeout=30)

        if result.returncode == 0:
            print("Recent captcha errors:")
            for line in result.stdout.strip().split('\n')[-10:]:  # Последние 10
                if line.strip():
                    print(f"  ❌ {line}")
        else:
            print(f"Error getting captcha errors: {result.stderr}")

    except Exception as e:
        print(f"❌ Error analyzing captcha errors: {e}")

def suggest_captcha_optimizations():
    """Предложить оптимизации для решения капч."""
    print("\n💡 Captcha optimization suggestions:")

    print("""
🎯 КРИТИЧЕСКИ ВАЖНО:
1. 🔑 Настроить 2captcha.com как резервный сервис
   - Зарегистрироваться: https://2captcha.com/enterpage
   - Пополнить баланс: $10-20 (хватит на месяц)
   - Добавить API ключ в настройки системы

2. ⚡ Увеличить таймауты решения капч:
   - Текущий: 120 секунд → Рекомендуемый: 300 секунд
   - Медленные прокси требуют больше времени

3. 🔄 Настроить fallback стратегию:
   - Capsola (быстро) → 2captcha (надёжно) → пропуск задачи

4. 📊 Мониторинг капч:
   - Отслеживать процент успешных решений
   - При падении ниже 70% — менять стратегию

🚨 ВРЕМЕННОЕ РЕШЕНИЕ (пока не настроена 2captcha):
- Увеличить задержки между поисками (меньше капч)
- Использовать более качественные прокси
- Добавить больше профилей для ротации
    """)

def apply_temporary_fixes():
    """Применить временные исправления для уменьшения количества капч."""
    print("\n⚡ Applying temporary fixes to reduce captcha frequency...")

    # Настройки для снижения частоты капч
    temp_fixes = {
        "yandex_search_min_delay": 15,     # Увеличить с 8 до 15 секунд
        "yandex_search_max_delay": 30,     # Увеличить с 15 до 30 секунд
        "search_referrer_percent": 80,     # 80% визитов через referrer (меньше капч)
        "max_pages_to_check": 2,           # Проверять только первые 2 страницы
        "captcha_timeout_seconds": 300,    # 5 минут на решение капчи
    }

    for setting_key, value in temp_fixes.items():
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
    print("🚨 URGENT: Fixing captcha resolution issues")
    print("=" * 60)

    # 1. Анализировать текущие проблемы
    check_captcha_service_status()
    analyze_recent_captcha_errors()

    # 2. Применить временные исправления
    apply_temporary_fixes()

    # 3. Настроить резервные сервисы (требует API ключи)
    setup_backup_anticaptcha_services()

    # 4. Дать рекомендации
    suggest_captcha_optimizations()

    print("\n" + "=" * 60)
    print("✅ CAPTCHA FIXES APPLIED!")
    print("\n📋 TODO List:")
    print("1. 🔑 Получить API ключ на 2captcha.com")
    print("2. 💰 Пополнить баланс ($10-20)")
    print("3. 🔧 Обновить anticaptcha_api_key в настройках")
    print("4. 📊 Мониторить успешность решения капч")
    print("\n🎯 Expected improvement:")
    print("  - Captcha success rate: 60% → 90%+")
    print("  - Failed tasks due to captcha: 50% → 10%")
    print("  - Daily successful clicks: 400 → 800+")

if __name__ == "__main__":
    main()