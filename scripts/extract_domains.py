#!/usr/bin/env python3
"""
Скрипт для извлечения уникальных доменов из файла nagul.txt
и обновления системы прогрева профилей.
"""

import re
import json
from urllib.parse import urlparse
from pathlib import Path


def extract_domains_from_file(file_path: str) -> set:
    """Извлекает уникальные домены из файла с ссылками."""
    domains = set()

    print(f"Читаем файл: {file_path}")

    with open(file_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            # Находим все URL в строке
            urls = re.findall(r'https?://[^\s,]+', line.strip())

            for url in urls:
                try:
                    # Парсим URL и извлекаем домен
                    parsed = urlparse(url)
                    domain = parsed.netloc.lower()

                    # Убираем www. если есть
                    if domain.startswith('www.'):
                        domain = domain[4:]

                    # Добавляем только если домен не пустой
                    if domain:
                        domains.add(domain)

                except Exception as e:
                    print(f"Ошибка парсинга URL '{url}' в строке {line_num}: {e}")
                    continue

            # Показываем прогресс каждые 10000 строк
            if line_num % 10000 == 0:
                print(f"Обработано {line_num} строк, найдено {len(domains)} уникальных доменов")

    return domains


def categorize_domains(domains: set) -> dict:
    """Категоризирует домены по типам сайтов."""
    categories = {
        'search_engines': [],
        'social_media': [],
        'news_media': [],
        'e_commerce': [],
        'educational': [],
        'entertainment': [],
        'technology': [],
        'food_cooking': [],
        'travel': [],
        'finance': [],
        'health': [],
        'automotive': [],
        'real_estate': [],
        'other': []
    }

    # Словари ключевых слов для каждой категории
    keywords = {
        'search_engines': ['google', 'yandex', 'bing', 'yahoo', 'rambler', 'mail.ru'],
        'social_media': ['vk.com', 'ok.ru', 'facebook', 'instagram', 'twitter', 'telegram', 'youtube', 'tiktok'],
        'news_media': ['rbc.ru', 'ria.ru', 'tass.ru', 'lenta.ru', 'gazeta.ru', 'kommersant.ru', 'vedomosti.ru'],
        'e_commerce': ['ozon.ru', 'wildberries.ru', 'market.yandex.ru', 'avito.ru', 'aliexpress', 'amazon'],
        'educational': ['wikipedia', 'wikihow', 'edu', 'coursera', 'edx'],
        'entertainment': ['kinopoisk.ru', 'ivi.ru', 'netflix', 'spotify', 'music.yandex.ru'],
        'technology': ['habr.com', 'github.com', 'stackoverflow', 'techcrunch'],
        'food_cooking': ['edimdoma.ru', 'povar.ru', 'gastronom.ru', 'food.ru'],
        'travel': ['booking.com', 'tripadvisor', 'aviasales.ru', 'tutu.ru'],
        'finance': ['sberbank.ru', 'tinkoff.ru', 'vtb.ru', 'alfabank.ru'],
        'health': ['who.int', 'mayo', 'webmd', 'zdorovie'],
        'automotive': ['auto.ru', 'drom.ru', 'cars.com'],
        'real_estate': ['cian.ru', 'domclick.ru', 'zillow']
    }

    for domain in domains:
        categorized = False

        for category, category_keywords in keywords.items():
            if any(keyword in domain.lower() for keyword in category_keywords):
                categories[category].append(domain)
                categorized = True
                break

        if not categorized:
            categories['other'].append(domain)

    # Сортируем домены в каждой категории
    for category in categories:
        categories[category].sort()

    return categories


def save_domains_to_files(domains: set, categorized_domains: dict):
    """Сохраняет домены в различные файлы."""

    # Создаем папку для данных если её нет
    data_dir = Path("data/warmup_sites")
    data_dir.mkdir(parents=True, exist_ok=True)

    # Сохраняем все уникальные домены
    all_domains_file = data_dir / "all_domains.txt"
    with open(all_domains_file, 'w', encoding='utf-8') as f:
        for domain in sorted(domains):
            f.write(f"https://{domain}\n")

    print(f"Все домены сохранены в: {all_domains_file}")
    print(f"Всего уникальных доменов: {len(domains)}")

    # Сохраняем категоризированные домены
    categories_file = data_dir / "domains_by_category.json"
    with open(categories_file, 'w', encoding='utf-8') as f:
        json.dump(categorized_domains, f, ensure_ascii=False, indent=2)

    # Сохраняем только качественные домены для прогрева
    quality_domains = []
    priority_categories = ['search_engines', 'social_media', 'news_media', 'e_commerce',
                          'educational', 'entertainment', 'technology']

    for category in priority_categories:
        quality_domains.extend(categorized_domains[category])

    # Добавляем некоторые из других категорий (не более 50 доменов из каждой)
    for category in ['food_cooking', 'travel', 'finance', 'health']:
        quality_domains.extend(categorized_domains[category][:50])

    # Добавляем 100 случайных доменов из категории 'other'
    import random
    other_domains = categorized_domains['other']
    if len(other_domains) > 100:
        quality_domains.extend(random.sample(other_domains, 100))
    else:
        quality_domains.extend(other_domains)

    # Сохраняем качественные домены для прогрева
    warmup_domains_file = data_dir / "warmup_domains.txt"
    with open(warmup_domains_file, 'w', encoding='utf-8') as f:
        for domain in sorted(set(quality_domains)):
            f.write(f"https://{domain}\n")

    print(f"Домены для прогрева сохранены в: {warmup_domains_file}")
    print(f"Количество доменов для прогрева: {len(set(quality_domains))}")

    # Выводим статистику по категориям
    print("\n=== СТАТИСТИКА ПО КАТЕГОРИЯМ ===")
    for category, domains_list in categorized_domains.items():
        if domains_list:
            print(f"{category}: {len(domains_list)} доменов")

    return warmup_domains_file


def main():
    """Главная функция скрипта."""

    print("🔍 Извлечение доменов из файла nagul.txt")
    print("=" * 50)

    # Путь к файлу nagul.txt
    nagul_file = Path("nagul.txt")

    if not nagul_file.exists():
        print(f"❌ Файл {nagul_file} не найден!")
        return

    # Извлекаем домены
    domains = extract_domains_from_file(nagul_file)

    print(f"\n✅ Извлечение завершено!")
    print(f"Найдено уникальных доменов: {len(domains)}")

    # Категоризируем домены
    print("\n📊 Категоризация доменов...")
    categorized_domains = categorize_domains(domains)

    # Сохраняем результаты
    print("\n💾 Сохранение результатов...")
    warmup_file = save_domains_to_files(domains, categorized_domains)

    print(f"\n🎉 Обработка завершена!")
    print(f"Файл для прогрева: {warmup_file}")

    # Показываем примеры доменов
    print(f"\n📋 Примеры извлеченных доменов:")
    sample_domains = sorted(list(domains))[:20]
    for i, domain in enumerate(sample_domains, 1):
        print(f"{i:2d}. {domain}")

    if len(domains) > 20:
        print(f"    ... и еще {len(domains) - 20} доменов")


if __name__ == "__main__":
    main()