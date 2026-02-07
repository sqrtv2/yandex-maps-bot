"""
Менеджер доменов для прогрева профилей.
Обеспечивает случайный выбор доменов для каждого профиля.
Независимый модуль без зависимостей от браузерных компонентов.
"""

import os
import random
import logging
from pathlib import Path
from typing import List, Set
from urllib.parse import urlparse

# Создаем логгер только если он еще не создан
try:
    logger = logging.getLogger(__name__)
except:
    import sys
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    logger = logging.getLogger(__name__)


class DomainManager:
    """Менеджер доменов для системы прогрева профилей."""

    def __init__(self):
        self.all_domains = []
        self.quality_domains = []
        self.used_domains_per_profile = {}  # profile_id -> set of domains
        self._load_domains()

    def _load_domains(self):
        """Загружает домены из файлов."""
        try:
            # Путь к файлам с доменами
            project_root = Path(__file__).parent.parent
            warmup_file = project_root / "data" / "warmup_sites" / "warmup_domains.txt"
            all_domains_file = project_root / "data" / "warmup_sites" / "all_domains.txt"

            # Загружаем качественные домены для прогрева
            if warmup_file.exists():
                with open(warmup_file, 'r', encoding='utf-8') as f:
                    self.quality_domains = [line.strip() for line in f if line.strip()]
                logger.info(f"Загружено {len(self.quality_domains)} качественных доменов для прогрева")
            else:
                logger.warning(f"Файл {warmup_file} не найден, используем дефолтные домены")
                self._use_default_domains()

            # Загружаем все домены
            if all_domains_file.exists():
                with open(all_domains_file, 'r', encoding='utf-8') as f:
                    self.all_domains = [line.strip() for line in f if line.strip()]
                logger.info(f"Загружено {len(self.all_domains)} всех доменов")
            else:
                self.all_domains = self.quality_domains.copy()

        except Exception as e:
            logger.error(f"Ошибка загрузки доменов: {e}")
            self._use_default_domains()

    def _use_default_domains(self):
        """Использует дефолтные домены если файлы не найдены."""
        self.quality_domains = [
            "https://google.com",
            "https://youtube.com",
            "https://yandex.ru",
            "https://vk.com",
            "https://mail.ru",
            "https://ok.ru",
            "https://rbc.ru",
            "https://lenta.ru",
            "https://avito.ru",
            "https://ozon.ru",
            "https://wildberries.ru",
            "https://market.yandex.ru",
            "https://kinopoisk.ru",
            "https://habr.com",
            "https://wikipedia.org",
            "https://ria.ru",
            "https://tass.ru",
            "https://gazeta.ru",
            "https://kommersant.ru"
        ]
        self.all_domains = self.quality_domains.copy()
        logger.info(f"Используем {len(self.quality_domains)} дефолтных доменов")

    def get_random_domains_for_profile(self, profile_id: int, count: int = 15, avoid_repeats: bool = True) -> List[str]:
        """
        Возвращает случайный набор доменов для профиля.

        Args:
            profile_id: ID профиля
            count: Количество доменов (по умолчанию 15)
            avoid_repeats: Избегать повторного использования доменов для этого профиля

        Returns:
            Список URL для посещения
        """
        try:
            if avoid_repeats and profile_id in self.used_domains_per_profile:
                # Получаем домены, которые уже использовались для этого профиля
                used_domains = self.used_domains_per_profile[profile_id]

                # Фильтруем неиспользованные домены
                available_domains = [d for d in self.quality_domains if d not in used_domains]

                # Если неиспользованных доменов мало, добавляем из всех доменов
                if len(available_domains) < count:
                    additional_needed = count - len(available_domains)
                    unused_from_all = [d for d in self.all_domains if d not in used_domains]

                    if unused_from_all:
                        additional_domains = random.sample(
                            unused_from_all,
                            min(additional_needed, len(unused_from_all))
                        )
                        available_domains.extend(additional_domains)

                # Если все еще недостаточно доменов, сбрасываем историю для этого профиля
                if len(available_domains) < count:
                    logger.info(f"Сбрасываем историю доменов для профиля {profile_id}")
                    self.used_domains_per_profile[profile_id] = set()
                    available_domains = self.quality_domains.copy()
            else:
                available_domains = self.quality_domains.copy()

            # Выбираем случайные домены
            selected_count = min(count, len(available_domains))
            selected_domains = random.sample(available_domains, selected_count)

            # Записываем использованные домены
            if profile_id not in self.used_domains_per_profile:
                self.used_domains_per_profile[profile_id] = set()

            self.used_domains_per_profile[profile_id].update(selected_domains)

            logger.info(f"Выбрано {len(selected_domains)} доменов для профиля {profile_id}")
            return selected_domains

        except Exception as e:
            logger.error(f"Ошибка выбора доменов для профиля {profile_id}: {e}")
            # Возвращаем дефолтный набор
            return [
                "https://google.com",
                "https://youtube.com",
                "https://yandex.ru",
                "https://vk.com",
                "https://mail.ru"
            ]

    def get_domains_by_category(self, categories: List[str], count: int = 10) -> List[str]:
        """
        Возвращает домены определенных категорий.

        Args:
            categories: Список категорий (social, news, ecommerce, etc.)
            count: Количество доменов

        Returns:
            Список доменов
        """
        categorized_domains = {
            'social': [d for d in self.quality_domains if any(keyword in d.lower()
                      for keyword in ['vk.com', 'ok.ru', 'youtube', 'instagram', 'facebook'])],
            'news': [d for d in self.quality_domains if any(keyword in d.lower()
                    for keyword in ['rbc.ru', 'lenta.ru', 'ria.ru', 'tass.ru', 'gazeta'])],
            'search': [d for d in self.quality_domains if any(keyword in d.lower()
                      for keyword in ['google', 'yandex', 'bing', 'mail.ru'])],
            'ecommerce': [d for d in self.quality_domains if any(keyword in d.lower()
                         for keyword in ['avito', 'ozon', 'wildberries', 'market'])],
            'education': [d for d in self.quality_domains if any(keyword in d.lower()
                         for keyword in ['wikipedia', 'wikihow', 'habr'])],
        }

        selected_domains = []
        for category in categories:
            if category in categorized_domains:
                category_domains = categorized_domains[category]
                if category_domains:
                    selected_domains.extend(category_domains[:count // len(categories)])

        # Дополняем случайными доменами если не хватает
        if len(selected_domains) < count:
            remaining = count - len(selected_domains)
            additional = [d for d in self.quality_domains if d not in selected_domains]
            if additional:
                selected_domains.extend(random.sample(additional, min(remaining, len(additional))))

        return selected_domains[:count]

    def get_stats(self) -> dict:
        """Возвращает статистику по доменам."""
        return {
            "total_quality_domains": len(self.quality_domains),
            "total_all_domains": len(self.all_domains),
            "profiles_with_history": len(self.used_domains_per_profile),
            "avg_domains_per_profile": sum(len(domains) for domains in self.used_domains_per_profile.values()) / max(len(self.used_domains_per_profile), 1)
        }

    def reset_profile_history(self, profile_id: int):
        """Сбрасывает историю доменов для конкретного профиля."""
        if profile_id in self.used_domains_per_profile:
            del self.used_domains_per_profile[profile_id]
            logger.info(f"История доменов сброшена для профиля {profile_id}")

    def reset_all_history(self):
        """Сбрасывает историю для всех профилей."""
        self.used_domains_per_profile.clear()
        logger.info("История доменов сброшена для всех профилей")

    def reload_domains(self):
        """Перезагружает домены из файлов."""
        self._load_domains()
        logger.info("Домены перезагружены из файлов")

    def validate_domain(self, url: str) -> bool:
        """Проверяет валидность домена."""
        try:
            result = urlparse(url)
            return all([result.scheme, result.netloc])
        except:
            return False


# Глобальный экземпляр менеджера доменов
domain_manager = DomainManager()