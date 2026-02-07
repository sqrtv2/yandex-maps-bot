"""
Warmup URL Manager - управление URL для прогрева профилей
"""
import random
import logging
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

# Добавляем корневую директорию в path для импортов
import sys
import os
from pathlib import Path

# Добавляем путь к проекту
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.database import get_db
from app.models import WarmupUrl

logger = logging.getLogger(__name__)


class WarmupUrlManager:
    """Менеджер для работы с URL прогрева."""

    def __init__(self):
        """Инициализация менеджера."""
        self._db_session = None

    def get_random_urls(self, count: int = 10, profile_id: Optional[int] = None) -> List[str]:
        """
        Получить случайные URLs для прогрева.

        Args:
            count: Количество URLs для возврата (по умолчанию 10)
            profile_id: ID профиля (для логирования и статистики)

        Returns:
            List[str]: Список случайных URL'ов
        """
        try:
            db = next(get_db())

            # Получаем активные URLs в случайном порядке
            urls_query = db.query(WarmupUrl).filter(WarmupUrl.is_active == True)

            # Подсчитываем общее количество активных URLs
            total_count = urls_query.count()

            if total_count == 0:
                logger.warning("No active warmup URLs found in database")
                return self._get_fallback_urls(count)

            if total_count < count:
                logger.info(f"Requested {count} URLs but only {total_count} available")
                count = total_count

            # Получаем случайные URLs через ORDER BY RANDOM()
            # Для больших таблиц это не самый эффективный метод,
            # но для наших целей подойдет
            urls = urls_query.order_by(func.random()).limit(count).all()

            # Извлекаем URLs (временно без обновления счетчиков для упрощения)
            result_urls = []
            for url_obj in urls:
                result_urls.append(url_obj.url)
                # Отключаем increment_usage() временно для исправления проблем с сессиями
                # url_obj.increment_usage()

            db.commit()
            db.close()

            logger.info(f"Selected {len(result_urls)} random URLs for profile {profile_id}")
            logger.debug(f"URLs: {result_urls[:3]}... (showing first 3)")

            return result_urls

        except Exception as e:
            logger.error(f"Error getting random URLs: {e}")
            return self._get_fallback_urls(count)

    def get_urls_by_domain(self, domains: List[str], max_per_domain: int = 2) -> List[str]:
        """
        Получить URLs по доменам.

        Args:
            domains: Список доменов
            max_per_domain: Максимум URLs на домен

        Returns:
            List[str]: Список URL'ов
        """
        try:
            db = next(get_db())
            urls = []

            for domain in domains:
                domain_urls = (db.query(WarmupUrl)
                             .filter(WarmupUrl.domain == domain, WarmupUrl.is_active == True)
                             .order_by(func.random())
                             .limit(max_per_domain)
                             .all())

                urls.extend([url.url for url in domain_urls])

            db.close()
            logger.info(f"Found {len(urls)} URLs for {len(domains)} domains")
            return urls

        except Exception as e:
            logger.error(f"Error getting URLs by domains: {e}")
            return []

    def get_popular_domains(self, limit: int = 50) -> List[str]:
        """
        Получить популярные домены (по количеству URL).

        Args:
            limit: Лимит доменов

        Returns:
            List[str]: Список доменов
        """
        try:
            db = next(get_db())

            # Группируем по доменам и считаем количество URLs
            domains = (db.query(WarmupUrl.domain, func.count(WarmupUrl.id).label('url_count'))
                      .filter(WarmupUrl.is_active == True, WarmupUrl.domain != '')
                      .group_by(WarmupUrl.domain)
                      .order_by(func.count(WarmupUrl.id).desc())
                      .limit(limit)
                      .all())

            db.close()
            result = [domain[0] for domain in domains]
            logger.info(f"Found {len(result)} popular domains")
            return result

        except Exception as e:
            logger.error(f"Error getting popular domains: {e}")
            return []

    def get_diverse_urls(self, count: int = 10, min_domains: int = 5) -> List[str]:
        """
        Получить разнообразные URLs (не более 2 с одного домена).

        Args:
            count: Количество URLs
            min_domains: Минимальное количество доменов

        Returns:
            List[str]: Список URL'ов
        """
        try:
            # Сначала получаем популярные домены
            popular_domains = self.get_popular_domains(min_domains * 2)

            if len(popular_domains) < min_domains:
                # Если популярных доменов мало, берем обычные случайные URLs
                return self.get_random_urls(count)

            # Выбираем случайные домены из популярных
            selected_domains = random.sample(popular_domains, min(len(popular_domains), min_domains))

            # Получаем URLs по выбранным доменам
            urls = []
            urls_per_domain = max(1, count // len(selected_domains))

            for domain in selected_domains:
                domain_urls = self.get_urls_by_domain([domain], urls_per_domain)
                urls.extend(domain_urls)

            # Дополняем случайными, если не хватает
            if len(urls) < count:
                additional_count = count - len(urls)
                additional_urls = self.get_random_urls(additional_count)
                # Избегаем дубликатов
                for url in additional_urls:
                    if url not in urls:
                        urls.append(url)
                        if len(urls) >= count:
                            break

            return urls[:count]

        except Exception as e:
            logger.error(f"Error getting diverse URLs: {e}")
            return self.get_random_urls(count)

    def get_statistics(self) -> dict:
        """Получить статистику по URL'ам."""
        try:
            db = next(get_db())

            total_urls = db.query(WarmupUrl).count()
            active_urls = db.query(WarmupUrl).filter(WarmupUrl.is_active == True).count()
            total_domains = db.query(WarmupUrl.domain).distinct().count()
            total_usage = db.query(func.sum(WarmupUrl.usage_count)).scalar() or 0

            # Топ 10 доменов по количеству URL
            top_domains = (db.query(WarmupUrl.domain, func.count(WarmupUrl.id))
                          .filter(WarmupUrl.domain != '')
                          .group_by(WarmupUrl.domain)
                          .order_by(func.count(WarmupUrl.id).desc())
                          .limit(10)
                          .all())

            db.close()

            return {
                'total_urls': total_urls,
                'active_urls': active_urls,
                'total_domains': total_domains,
                'total_usage': total_usage,
                'top_domains': [{'domain': d[0], 'url_count': d[1]} for d in top_domains]
            }

        except Exception as e:
            logger.error(f"Error getting statistics: {e}")
            return {}

    def _get_fallback_urls(self, count: int) -> List[str]:
        """Fallback URLs если база данных недоступна."""
        fallback_urls = [
            "https://google.com",
            "https://yandex.ru",
            "https://youtube.com",
            "https://wikipedia.org",
            "https://github.com",
            "https://stackoverflow.com",
            "https://habr.com",
            "https://vk.com",
            "https://mail.ru",
            "https://vc.ru",
            "https://lenta.ru",
            "https://rbc.ru",
            "https://tass.ru",
            "https://rt.com",
            "https://dzen.ru"
        ]

        # Возвращаем случайные URLs из fallback списка
        selected = random.sample(fallback_urls, min(count, len(fallback_urls)))
        logger.warning(f"Using fallback URLs: {len(selected)} URLs selected")
        return selected

    def mark_url_inactive(self, url: str) -> bool:
        """
        Отметить URL как неактивный (если он недоступен).

        Args:
            url: URL для деактивации

        Returns:
            bool: True если URL был деактивирован
        """
        try:
            db = next(get_db())

            url_obj = db.query(WarmupUrl).filter(WarmupUrl.url == url).first()
            if url_obj:
                url_obj.is_active = False
                db.commit()
                db.close()
                logger.info(f"Marked URL as inactive: {url}")
                return True

            db.close()
            return False

        except Exception as e:
            logger.error(f"Error marking URL as inactive: {e}")
            return False


# Создаем глобальный экземпляр менеджера
warmup_url_manager = WarmupUrlManager()


def get_warmup_urls(count: int = 10, profile_id: Optional[int] = None, strategy: str = "diverse") -> List[str]:
    """
    Удобная функция для получения URLs для прогрева.

    Args:
        count: Количество URLs
        profile_id: ID профиля (для логирования)
        strategy: Стратегия выбора ('random', 'diverse', 'popular')

    Returns:
        List[str]: Список URL'ов для прогрева
    """
    if strategy == "diverse":
        return warmup_url_manager.get_diverse_urls(count)
    elif strategy == "popular":
        domains = warmup_url_manager.get_popular_domains(count)
        return warmup_url_manager.get_urls_by_domain(domains, 1)
    else:  # random
        return warmup_url_manager.get_random_urls(count, profile_id)


if __name__ == "__main__":
    # Тестирование
    manager = WarmupUrlManager()

    print("🧪 Тестирование Warmup URL Manager")
    print("=" * 50)

    # Статистика
    stats = manager.get_statistics()
    print(f"📊 Статистика:")
    for key, value in stats.items():
        if key != 'top_domains':
            print(f"   {key}: {value}")

    print(f"\n🔥 Топ доменов:")
    for domain_info in stats.get('top_domains', [])[:5]:
        print(f"   {domain_info['domain']}: {domain_info['url_count']} URLs")

    # Тест получения случайных URL
    print(f"\n🎲 Случайные 5 URLs:")
    random_urls = manager.get_random_urls(5)
    for i, url in enumerate(random_urls, 1):
        print(f"   {i}. {url}")

    # Тест разнообразных URL
    print(f"\n🎨 Разнообразные 8 URLs:")
    diverse_urls = manager.get_diverse_urls(8)
    for i, url in enumerate(diverse_urls, 1):
        print(f"   {i}. {url}")

    print(f"\n✅ Тестирование завершено!")