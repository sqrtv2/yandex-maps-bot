#!/usr/bin/env python3
"""
Тестовый скрипт для проверки работы системы управления доменами.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.domain_manager import domain_manager


def test_domain_manager():
    """Тестирует работу менеджера доменов."""

    print("🧪 ТЕСТИРОВАНИЕ СИСТЕМЫ ДОМЕНОВ")
    print("=" * 50)

    # Тест 1: Получение статистики
    print("\n1️⃣ Тест получения статистики:")
    stats = domain_manager.get_stats()
    print(f"   ✓ Качественных доменов: {stats['total_quality_domains']}")
    print(f"   ✓ Всего доменов: {stats['total_all_domains']}")
    print(f"   ✓ Профилей с историей: {stats['profiles_with_history']}")
    print(f"   ✓ Среднее доменов/профиль: {stats['avg_domains_per_profile']:.1f}")

    # Тест 2: Получение доменов для разных профилей
    print("\n2️⃣ Тест получения доменов для профилей:")

    for profile_id in [1, 2, 3]:
        domains = domain_manager.get_random_domains_for_profile(
            profile_id=profile_id,
            count=5,
            avoid_repeats=True
        )
        print(f"   Профиль {profile_id}: получено {len(domains)} доменов")
        for i, domain in enumerate(domains, 1):
            clean_domain = domain.replace('https://', '').replace('http://', '')
            print(f"     {i}. {clean_domain}")

    # Тест 3: Проверка различных доменов для разных профилей
    print("\n3️⃣ Тест уникальности доменов по профилям:")

    profile_domains = {}
    for profile_id in [1, 2, 3]:
        domains = domain_manager.get_random_domains_for_profile(profile_id, count=10, avoid_repeats=True)
        profile_domains[profile_id] = set(domains)

    # Сравниваем пересечения
    for i in range(1, 4):
        for j in range(i+1, 4):
            intersection = profile_domains[i] & profile_domains[j]
            intersection_percent = len(intersection) / len(profile_domains[i]) * 100
            print(f"   Пересечение профиль {i} ↔ профиль {j}: {len(intersection)} доменов ({intersection_percent:.1f}%)")

    # Тест 4: Получение доменов по категориям
    print("\n4️⃣ Тест доменов по категориям:")

    categories_to_test = ['social', 'news', 'search', 'ecommerce']
    for category in categories_to_test:
        domains = domain_manager.get_domains_by_category([category], count=5)
        print(f"   Категория '{category}': {len(domains)} доменов")
        for domain in domains[:3]:
            clean_domain = domain.replace('https://', '').replace('http://', '')
            print(f"     • {clean_domain}")

    # Тест 5: Проверка повторного использования после исчерпания
    print("\n5️⃣ Тест сброса истории после исчерпания:")

    profile_id = 99
    initial_stats = domain_manager.get_stats()

    # Получаем много доменов, чтобы исчерпать список
    all_received_domains = []
    for round_num in range(1, 6):
        domains = domain_manager.get_random_domains_for_profile(profile_id, count=50, avoid_repeats=True)
        all_received_domains.extend(domains)
        print(f"   Раунд {round_num}: получено {len(domains)} доменов (всего: {len(all_received_domains)})")

    # Тест 6: Сброс истории
    print("\n6️⃣ Тест сброса истории:")

    domain_manager.reset_profile_history(profile_id)
    print(f"   ✓ История сброшена для профиля {profile_id}")

    final_stats = domain_manager.get_stats()
    print(f"   ✓ Профилей с историей до: {initial_stats['profiles_with_history']}")
    print(f"   ✓ Профилей с историей после: {final_stats['profiles_with_history']}")

    print("\n🎉 ВСЕ ТЕСТЫ ВЫПОЛНЕНЫ!")
    return True


if __name__ == "__main__":
    try:
        test_domain_manager()
        print("\n✅ Система доменов работает корректно!")
    except Exception as e:
        print(f"\n❌ Ошибка в тестах: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)