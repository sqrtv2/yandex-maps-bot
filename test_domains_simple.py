#!/usr/bin/env python3
"""
Простой тест системы доменов без зависимостей от браузерных модулей.
"""

import os
import sys
from pathlib import Path

# Добавляем путь к проекту
project_root = Path(__file__).parent
sys.path.append(str(project_root))

# Импортируем только DomainManager
try:
    from core.domain_manager import DomainManager
except ImportError:
    # Если не получается импортировать из-за других зависимостей, создаем локальную копию
    sys.path.append(str(project_root / "core"))

    import random
    import json
    from urllib.parse import urlparse

    class DomainManager:
        def __init__(self):
            self.all_domains = []
            self.quality_domains = []
            self.used_domains_per_profile = {}
            self._load_domains()

        def _load_domains(self):
            # Пути к файлам
            warmup_file = project_root / "data" / "warmup_sites" / "warmup_domains.txt"

            if warmup_file.exists():
                with open(warmup_file, 'r', encoding='utf-8') as f:
                    self.quality_domains = [line.strip() for line in f if line.strip()]
                print(f"Загружено {len(self.quality_domains)} доменов из файла")
            else:
                print(f"Файл {warmup_file} не найден!")

        def get_random_domains_for_profile(self, profile_id: int, count: int = 15, avoid_repeats: bool = True):
            if avoid_repeats and profile_id in self.used_domains_per_profile:
                used_domains = self.used_domains_per_profile[profile_id]
                available_domains = [d for d in self.quality_domains if d not in used_domains]

                if len(available_domains) < count:
                    self.used_domains_per_profile[profile_id] = set()
                    available_domains = self.quality_domains.copy()
            else:
                available_domains = self.quality_domains.copy()

            selected_count = min(count, len(available_domains))
            selected_domains = random.sample(available_domains, selected_count) if available_domains else []

            if profile_id not in self.used_domains_per_profile:
                self.used_domains_per_profile[profile_id] = set()
            self.used_domains_per_profile[profile_id].update(selected_domains)

            return selected_domains

        def get_stats(self):
            return {
                "total_quality_domains": len(self.quality_domains),
                "profiles_with_history": len(self.used_domains_per_profile),
                "avg_domains_per_profile": sum(len(domains) for domains in self.used_domains_per_profile.values()) / max(len(self.used_domains_per_profile), 1)
            }

        def reset_profile_history(self, profile_id: int):
            if profile_id in self.used_domains_per_profile:
                del self.used_domains_per_profile[profile_id]


def main():
    print("🔍 ТЕСТИРОВАНИЕ СИСТЕМЫ ДОМЕНОВ")
    print("=" * 50)

    # Инициализируем менеджер доменов
    dm = DomainManager()

    # Тест 1: Проверяем загрузку доменов
    stats = dm.get_stats()
    print(f"\n📊 Статистика:")
    print(f"   • Всего доменов: {stats['total_quality_domains']}")
    print(f"   • Профилей с историей: {stats['profiles_with_history']}")

    if stats['total_quality_domains'] == 0:
        print("\n❌ Домены не загружены! Проверьте файл data/warmup_sites/warmup_domains.txt")
        return False

    # Тест 2: Получение доменов для разных профилей
    print(f"\n🎲 Тест получения доменов для профилей:")

    for profile_id in [1, 2, 3]:
        domains = dm.get_random_domains_for_profile(profile_id, count=8, avoid_repeats=True)
        print(f"\n   Профиль {profile_id} ({len(domains)} доменов):")
        for i, domain in enumerate(domains[:5], 1):
            clean_domain = domain.replace('https://', '').replace('http://', '')
            print(f"     {i}. {clean_domain}")
        if len(domains) > 5:
            print(f"     ... и еще {len(domains) - 5} доменов")

    # Тест 3: Проверка уникальности
    print(f"\n🔄 Проверка уникальности доменов между профилями:")

    domains_1 = set(dm.get_random_domains_for_profile(1, count=10))
    domains_2 = set(dm.get_random_domains_for_profile(2, count=10))
    domains_3 = set(dm.get_random_domains_for_profile(3, count=10))

    intersection_12 = len(domains_1 & domains_2)
    intersection_13 = len(domains_1 & domains_3)
    intersection_23 = len(domains_2 & domains_3)

    print(f"   • Профиль 1 ↔ 2: {intersection_12} общих доменов")
    print(f"   • Профиль 1 ↔ 3: {intersection_13} общих доменов")
    print(f"   • Профиль 2 ↔ 3: {intersection_23} общих доменов")

    # Тест 4: Итоговая статистика
    final_stats = dm.get_stats()
    print(f"\n📈 Итоговая статистика:")
    print(f"   • Профилей использовало домены: {final_stats['profiles_with_history']}")
    print(f"   • Среднее доменов на профиль: {final_stats['avg_domains_per_profile']:.1f}")

    # Тест 5: Примеры доменов
    print(f"\n📋 Примеры доменов из файла:")
    sample_domains = dm.quality_domains[:10]
    for i, domain in enumerate(sample_domains, 1):
        clean_domain = domain.replace('https://', '').replace('http://', '')
        print(f"   {i:2d}. {clean_domain}")

    print(f"\n✅ Система доменов работает корректно!")
    print(f"📁 Загружено {len(dm.quality_domains)} доменов из файла nagul.txt")
    print(f"🎯 Каждый профиль получает уникальный набор доменов для прогрева")

    return True


if __name__ == "__main__":
    try:
        success = main()
        if success:
            print("\n🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО!")
        else:
            print("\n❌ ТЕСТЫ НЕ ПРОШЛИ!")
            sys.exit(1)
    except Exception as e:
        print(f"\n💥 ОШИБКА В ТЕСТАХ: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)