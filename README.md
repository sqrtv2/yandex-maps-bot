# Система автоматизации посещения профилей Яндекс Карт

## 📋 Обзор системы

Комплексное решение для автоматизации посещения профилей на Яндекс Картах с использованием браузерных профилей, прокси, антикапчи и веб-интерфейса управления.

## 🚀 Функциональность

1. **Создание профилей браузера** с эмуляцией разных отпечатков (fingerprinting: UA, viewport, WebGL, canvas, timezone)
2. **AI-персоны** — генерация реалистичных персон через Google Gemini для естественного поведения
3. **Прогрев профилей** через посещение 18 000+ URL (Яндекс-экосистема, рунет, международные сайты)
4. **Посещение профилей Яндекс Карт** — автоматизация визитов с имитацией действий пользователя
5. **Клики из Яндекс Поиска** — поиск по ключевому слову → нахождение и клик на целевой сайт
6. **Парсер Яндекс Карт** — сбор данных компаний (название, телефон, email, сайт, адрес)
7. **Email-рассылки** — отправка персонализированных писем по спарсенным компаниям с ротацией SMTP
8. **Wordstat-аналитика** — получение частотности ключевых слов через Yandex Wordstat API
9. **Интеграция прокси и антикапчи** — ротация IP, решение SmartCaptcha/reCAPTCHA через 2captcha, anti-captcha, Capsola
10. **Веб-интерфейс** — панель управления с дашбордом, аналитикой, мониторингом задач
11. **Автоматический планировщик** — расписание визитов, очистка зависших задач, watchdog очередей

## 🛠 Технологический стек

### Backend
- **Python 3.11** — основной язык (Docker-образ: `python:3.11-slim`)
- **FastAPI** — веб-фреймворк для REST API + Jinja2 шаблоны
- **SQLAlchemy 2.0 + SQLite/PostgreSQL** — ORM и база данных (WAL-режим для SQLite)
- **Celery 5.3 + Redis 7** — очереди фоновых задач (5 отдельных воркеров)
- **undetected-chromedriver** — браузерная автоматизация с обходом антибот-детекции
- **Selenium WebDriver** — управление Chrome через ActionChains
- **fake-useragent + Faker** — генерация user-agents, персональных данных
- **Google Gemini (Vertex AI)** — генерация AI-персон для профилей
- **Pillow** — обработка изображений капч
- **Pydantic Settings** — конфигурация через переменные окружения

### Frontend
- **HTML5/CSS3/JavaScript** — веб-интерфейс (12 страниц)
- **Bootstrap 5** — CSS фреймворк
- **Chart.js** — графики и статистика
- **WebSockets** — обновления в реальном времени

### Внешние сервисы
- **Redis 7** — брокер Celery + кэш + хранилище сессий авторизации
- **PostgreSQL 15** — основная БД в Docker (SQLite для локальной разработки)
- **2captcha / anti-captcha / Capsola** — решение SmartCaptcha, reCAPTCHA v2/v3, PazlCaptcha
- **Google Gemini API** — генерация AI-персон
- **Yandex Wordstat API** — частотность ключевых слов
- **Прокси провайдеры** — ротация IP адресов

### Инфраструктура
- **Docker + Docker Compose** — 12 сервисов в контейнерах
- **Nginx** — reverse proxy (порты 80/443)
- **Prometheus + Grafana** — мониторинг метрик
- **Flower** — мониторинг Celery на порту 5555
- **Xvfb** — виртуальный дисплей для headless Chrome в Docker

## 🏗 Архитектура системы

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Web Interface │───▶│   FastAPI API   │───▶│   Task Queue    │
│  (12 страниц)   │    │  + Auth (Redis) │    │  (Celery + Beat)│
└─────────────────┘    └─────────────────┘    └─────────────────┘
        │                      │                        │
        ▼                      ▼                        ▼
┌─────────────────┐    ┌─────────────────┐    ┌──────────────────────────┐
│     Nginx       │    │   PostgreSQL    │    │     5 Celery Workers     │
│  (reverse proxy)│    │    / SQLite     │    │  ┌────────────────────┐  │
└─────────────────┘    └─────────────────┘    │  │ warmup (conc=15)   │  │
                              │               │  │ yandex_maps (c=2)  │  │
                              ▼               │  │ yandex_search (c=6)│  │
                       ┌─────────────────┐    │  │ parser (c=2)       │  │
                       │  16 SQLAlchemy  │    │  │ maintenance/proxy  │  │
                       │     моделей     │    │  └────────────────────┘  │
                       └─────────────────┘    └──────────────────────────┘
                                                        │
                              ┌──────────────────────────┤
                              ▼                          ▼
                       ┌─────────────────┐    ┌─────────────────┐
                       │ Chrome Browsers │    │  Anti-Captcha   │
                       │ (undetected-cd) │    │ 2captcha/Capsola│
                       │  + Proxy Ext    │    │  + SmartCaptcha │
                       └─────────────────┘    └─────────────────┘
                              │
                       ┌──────┴──────┐
                       ▼             ▼
                ┌────────────┐ ┌──────────┐
                │  Яндекс    │ │ Яндекс   │
                │   Карты    │ │  Поиск   │
                └────────────┘ └──────────┘
```

## 📁 Структура проекта

```
├── app/                          # Основное приложение FastAPI
│   ├── main.py                   # Точка входа, lifespan, middleware
│   ├── config.py                 # Pydantic Settings (все env-переменные)
│   ├── database.py               # SQLAlchemy engine, session, WAL-режим
│   └── models/                   # SQLAlchemy модели (16 таблиц)
│       ├── browser_profile.py    # Профили браузеров + fingerprint
│       ├── proxy.py              # Прокси серверы
│       ├── task.py               # Задачи (warmup, visit, search)
│       ├── user_settings.py      # Настройки приложения (key-value)
│       ├── yandex_target.py      # Таргеты Яндекс Карт (URL + расписание)
│       ├── yandex_search_target.py # Таргеты Яндекс Поиска (домен + ключи)
│       ├── profile_target_visit.py # Связь профиль ↔ таргет Карт
│       ├── profile_search_visit.py # Связь профиль ↔ таргет Поиска
│       ├── search_position_history.py # История позиций в выдаче
│       ├── keyword_frequency.py  # Кэш частотности Wordstat
│       ├── warmup_url.py         # URL для прогрева (18 000+)
│       ├── parsed_company.py     # Спарсенные компании с Яндекс Карт
│       ├── parse_task.py         # Задачи парсинга
│       └── mailing.py            # SmtpAccount, MailingCampaign, MailingMessage
│
├── core/                         # Бизнес-логика (движки)
│   ├── browser_manager.py        # Управление Chrome (undetected-chromedriver)
│   ├── profile_generator.py      # Генерация fingerprint (UA, WebGL, canvas, timezone)
│   ├── proxy_manager.py          # Ротация прокси, health checks, round-robin
│   ├── captcha_solver.py         # 2captcha / anti-captcha (image, reCAPTCHA v2/v3)
│   ├── capsola_solver.py         # Capsola Cloud API (SmartCaptcha, PazlCaptcha)
│   ├── ai_persona_generator.py   # AI-персоны через Google Gemini (Vertex AI)
│   ├── domain_manager.py         # Пул доменов для прогрева
│   ├── warmup_url_manager.py     # Менеджер 18 000+ URL из БД
│   └── wordstat_manager.py       # Yandex Wordstat API (broad/phrase/exact)
│
├── tasks/                        # Celery-задачи
│   ├── celery_app.py             # Конфигурация, маршрутизация очередей, beat-расписание
│   ├── warmup.py                 # Прогрев профилей (Яндекс + рунет + международные)
│   ├── yandex_maps.py            # Визиты на карточки Яндекс Карт
│   ├── yandex_search.py          # Клики из Яндекс Поиска (поиск → клик на сайт)
│   └── yandex_scheduler.py       # Планировщик визитов + cleanup зависших задач
│
├── parser/                       # Парсер Яндекс Карт
│   ├── __init__.py               # parse_yandex_maps_search(), extract_emails_from_websites()
│   ├── routes.py                 # API: /parser, /api/parser/tasks, batch
│   └── tasks.py                  # Celery: parse_yandex_maps_task
│
├── mailing/                      # Email-рассылки
│   ├── __init__.py               # send_email(), get_available_smtp_account(), personalize_text()
│   ├── routes.py                 # API: /mailing, SMTP-аккаунты, кампании
│   └── tasks.py                  # Celery: run_campaign_task (ротация SMTP)
│
├── web/                          # Веб-интерфейс
│   ├── auth.py                   # Session-аутентификация (Redis + cookie)
│   ├── routes.py                 # HTML-страницы + API эндпоинты
│   ├── templates/                # Jinja2 шаблоны (12 страниц)
│   │   ├── base.html             # Базовый layout
│   │   ├── login.html            # Авторизация
│   │   ├── index.html            # Дашборд
│   │   ├── profiles.html         # Управление профилями
│   │   ├── proxies.html          # Управление прокси
│   │   ├── tasks.html            # Мониторинг задач
│   │   ├── settings.html         # Настройки приложения
│   │   ├── yandex_targets.html   # Таргеты Яндекс Карт
│   │   ├── yandex_search.html    # Таргеты Яндекс Поиска
│   │   ├── search_analytics.html # Аналитика поисковых позиций
│   │   ├── domains.html          # Управление доменами прогрева
│   │   └── parser.html           # Парсер Яндекс Карт
│   └── static/                   # CSS, JS, изображения
│
├── scripts/                      # Утилиты и скрипты обслуживания
│   ├── cleanup_chrome.sh         # Убийство зависших Chrome/ChromeDriver
│   ├── status_check.sh           # Полная проверка системы (API, Celery, Redis, DB)
│   ├── check_web.sh              # Health check FastAPI
│   ├── warmup_all_profiles.py    # Прогрев всех непрогретых профилей
│   ├── import_warmup_urls.py     # Импорт URL из nagul.txt в БД
│   ├── extract_domains.py        # Извлечение доменов из nagul.txt
│   ├── fix_user_agents.py        # Обновление UA под текущий Chrome
│   ├── monitor_chrome.py         # Мониторинг Chrome-процессов
│   ├── reset_and_start.py        # Сброс застрявших профилей
│   ├── run_scheduler_now.py      # Ручной запуск планировщика
│   ├── insert_capsola_settings.py # Вставка настроек Capsola в БД
│   └── visit_yandex.py           # Ручной визит на Яндекс Карты
│
├── data/                         # Данные
│   └── warmup_sites/             # Файлы с URL для прогрева
│       ├── warmup_domains.txt    # Качественные домены
│       ├── all_domains.txt       # Все домены
│       ├── nagul.txt             # Исходный файл с 18 000+ URL
│       └── domains_by_category.json # Домены по категориям
│
├── browser_profiles/             # Данные Chrome-профилей (cookies, localStorage)
├── proxy_ext/                    # Chrome-расширения для прокси
├── screenshots/                  # Скриншоты визитов
├── logs/                         # Логи приложения
│
├── docker-compose.yml            # Docker Compose (12 сервисов)
├── Dockerfile                    # python:3.11-slim + Chrome + Xvfb
├── nginx.conf                    # Конфигурация reverse proxy
├── prometheus.yml                # Мониторинг метрик
├── requirements.txt              # Python зависимости
├── .env.example                  # Шаблон переменных окружения
└── deploy.sh                     # Скрипт деплоя
```

## ✅ Статус реализации

### Этап 1: Базовая инфраструктура ✅
- ✅ Настройка структуры проекта
- ✅ Создание SQLAlchemy моделей
- ✅ FastAPI приложение с REST API
- ✅ Подключение к базе данных

### Этап 2: Система профилей браузера ✅
- ✅ Генератор профилей с fingerprinting
- ✅ Менеджер браузеров с undetected-chromedriver
- ✅ Эмуляция человеческого поведения

### Этап 3: Прокси и антикапча ✅
- ✅ Менеджер прокси серверов с health checks
- ✅ Интеграция с 2captcha и anti-captcha
- ✅ Автоматическая ротация прокси

### Этап 4: Прогрев профилей ✅
- ✅ Стратегии посещения популярных сайтов
- ✅ Имитация естественного поведения пользователя
- ✅ Система мониторинга состояния профилей

### Этап 5: Работа с Яндекс Картами ✅
- ✅ Парсинг ссылок профилей организаций
- ✅ Автоматизация действий на странице
- ✅ Обход защитных механизмов и капч

### Этап 6: Веб-интерфейс ✅
- ✅ Bootstrap 5 панель управления
- ✅ Управление профилями и прокси
- ✅ Мониторинг задач и статистика

### Этап 7: Фоновые задачи ✅
- ✅ Celery воркеры с Redis
- ✅ Очередь задач с приоритетами
- ✅ Система уведомлений через WebSocket

### Этап 8: Развертывание ✅
- ✅ Docker контейнеризация (python:3.11-slim + Chrome + Xvfb)
- ✅ Docker Compose — 12 сервисов
- ✅ Мониторинг (Prometheus + Grafana + Flower)
- ✅ Nginx reverse proxy

### Этап 9: Яндекс Поиск ✅
- ✅ Клик-через из Яндекс Поиска (поиск → клик на целевой сайт)
- ✅ Трекинг позиций в выдаче (SearchPositionHistory)
- ✅ Автоматический планировщик поисковых визитов

### Этап 10: Парсер Яндекс Карт ✅
- ✅ Selenium-парсинг карточек организаций
- ✅ Batch-парсинг по регионам
- ✅ Извлечение email с сайтов компаний

### Этап 11: Email-рассылки ✅
- ✅ SMTP-аккаунты с ротацией и дневными лимитами
- ✅ Персонализация шаблонов ({company_name}, {город}, {категория})
- ✅ Celery-задачи для фоновой отправки

### Этап 12: AI и аналитика ✅
- ✅ AI-персоны через Google Gemini (Vertex AI)
- ✅ Yandex Wordstat частотность ключевых слов
- ✅ Capsola Cloud для SmartCaptcha/PazlCaptcha
- ✅ Аналитика поисковых позиций

## 🎯 Ключевые компоненты

### Celery-задачи и очереди

| Очередь | Воркер | Concurrency | Задачи |
|---------|--------|-------------|--------|
| `warmup` | celery_warmup | 15 | Прогрев профилей, переогрев, cleanup Chrome |
| `yandex_maps` | celery_yandex_maps | 2 | Визиты на карточки Яндекс Карт |
| `yandex_search` | celery_yandex_search | 6 | Клики из Яндекс Поиска |
| `parser` | celery_parser | 2 | Парсинг компаний с Яндекс Карт |
| `proxy`, `maintenance` | celery_warmup | 15 | Health checks прокси, очистка задач |

### Celery Beat — периодические задачи (14 шт.)

| Задача | Расписание |
|--------|-----------|
| `schedule_visits` — планировщик визитов Карт | Каждые 5 мин |
| `schedule_search_visits` — планировщик кликов Поиска | Каждые 5 мин |
| `auto_schedule_initial_warmup` — автопрогрев новых профилей | Каждые 5 мин |
| `queue_watchdog` — мониторинг очередей | Каждые 3 мин |
| `periodic_rewarmup` — переогрев профилей | Каждые 15 мин |
| `auto_fix_stuck_processes` — починка зависших процессов | Каждые 10 мин |
| `cleanup_stale_chromedriver_processes` — очистка ChromeDriver | Каждые 10 мин |
| `check_all_proxies` — health check прокси | Каждые 15 мин |
| `update_proxy_statistics` — обновление статистики прокси | Каждые 30 мин |
| `cleanup_used_profiles` — очистка использованных профилей | Каждые 30 мин |
| `daily_stats_reset` — сброс дневной статистики Карт | Ежедневно 00:00 |
| `daily_search_stats_reset` — сброс статистики Поиска | Ежедневно 00:00 |
| `profile_maintenance` — обслуживание профилей | Ежедневно 01:00 |
| `cleanup_old_tasks` — очистка старых задач | Ежедневно 02:00 |

### Docker-сервисы (12 контейнеров)

| Сервис | Образ / Команда | Порт | Описание |
|--------|----------------|------|----------|
| **app** | FastAPI (uvicorn) | 8000 | Веб-приложение + API |
| **celery_warmup** | celery worker | — | Прогрев + maintenance (conc=15, 32GB RAM) |
| **celery_yandex_maps** | celery worker | — | Визиты Яндекс Карт (conc=2, 16GB RAM) |
| **celery_yandex_search** | celery worker | — | Клики из Поиска (conc=6, 32GB RAM) |
| **celery_parser** | celery worker | — | Парсинг (conc=2, 8GB RAM) |
| **celery_beat** | celery beat | — | Планировщик периодических задач |
| **postgres** | postgres:15-alpine | 5432 | База данных |
| **redis** | redis:7-alpine | 6379 | Брокер + кэш (AOF) |
| **flower** | celery flower | 5555 | Мониторинг Celery |
| **nginx** | nginx:alpine | 80, 443 | Reverse proxy |
| **prometheus** | prom/prometheus | 9090 | Метрики |
| **grafana** | grafana/grafana | 3000 | Дашборды мониторинга |

### Модели данных (16 таблиц)

| Модель | Таблица | Назначение |
|--------|---------|-----------|
| `BrowserProfile` | `browser_profiles` | Профили браузеров с fingerprint |
| `ProxyServer` | `proxy_servers` | Прокси с health check статистикой |
| `Task` | `tasks` | Все задачи (warmup, visit, search, parse) |
| `UserSettings` | `user_settings` | Настройки приложения (key-value) |
| `YandexMapTarget` | `yandex_map_targets` | Таргеты Карт (URL + расписание визитов) |
| `YandexSearchTarget` | `yandex_search_targets` | Таргеты Поиска (домен + ключевые слова) |
| `ProfileTargetVisit` | `profile_target_visits` | Какой профиль какой таргет Карт посетил |
| `ProfileSearchVisit` | `profile_search_visits` | Какой профиль какой таргет Поиска кликнул |
| `SearchPositionHistory` | `search_position_history` | История позиций сайта в выдаче |
| `KeywordFrequency` | `keyword_frequencies` | Кэш Wordstat (broad/phrase/exact) |
| `WarmupUrl` | `warmup_urls` | 18 000+ URL для прогрева |
| `ParsedCompany` | `parsed_companies` | Компании спарсенные с Яндекс Карт |
| `ParseTask` | `parse_tasks` | Задачи парсинга |
| `SmtpAccount` | `smtp_accounts` | SMTP аккаунты с лимитами |
| `MailingCampaign` | `mailing_campaigns` | Кампании рассылок |
| `MailingMessage` | `mailing_messages` | Отдельные письма кампаний |

### Аутентификация

- Session-based через cookie `session_token` (срок жизни 7 дней)
- Сессии хранятся в **Redis** (ключ `web_session:*`)
- Credentials из ENV: `AUTH_USERNAME` (default: admin), `AUTH_PASSWORD` (default: admin123)
- Fallback на in-memory dict если Redis недоступен

## 📊 Веб-интерфейс

### Страницы (12 шт.)

| Страница | URL | Описание |
|----------|-----|----------|
| Авторизация | `/login` | Вход по логину/паролю |
| Дашборд | `/dashboard` | Статистика профилей, задач, графики активности |
| Профили | `/profiles` | Создание, прогрев, bulk-операции, Clear All |
| Прокси | `/proxies` | Добавление, тестирование, статистика прокси |
| Задачи | `/tasks` | Мониторинг всех задач, логи выполнения |
| Настройки | `/settings` | Конфигурация приложения (антикапча, таймауты) |
| Таргеты Карт | `/yandex-targets` | Управление URL профилей Яндекс Карт |
| Таргеты Поиска | `/yandex-search` | Домены + ключевые слова для кликов |
| Аналитика | `/search-analytics` | Графики позиций в Яндекс Поиске |
| Домены | `/domains` | Управление доменами для прогрева |
| Парсер | `/parser` | Запуск парсинга, batch по регионам, экспорт |
| Рассылки | `/mailing` | SMTP-аккаунты, кампании, шаблоны писем |

### Главная панель
- Статистика активных профилей
- Количество выполненных задач
- Процент успешных посещений
- Графики активности

### Управление профилями
- Создание и настройка профилей
- Просмотр статуса прогрева
- Экспорт/импорт настроек

### Настройка прокси
- Добавление прокси серверов
- Тестирование соединения
- Статистика использования

### Управление задачами
- Добавление ссылок Яндекс Карт
- Настройка расписания
- Мониторинг выполнения
- Просмотр логов

## 🚀 Установка и запуск

### Системные требования

#### Минимальные (локальная разработка)
- Python 3.11+
- Chrome/Chromium браузер
- Redis
- 4GB RAM
- 10GB свободного места

#### Рекомендуемые (Docker production)
- Docker + Docker Compose
- 32GB+ RAM (5 Celery-воркеров + Chrome-инстансы)
- 8+ CPU ядер
- 50GB+ SSD
- Стабильные прокси-серверы

### 1. Клонирование репозитория
```bash
git clone <repository_url>
cd PF
```

### 2. Настройка окружения
```bash
# Создание виртуального окружения
python -m venv venv
source venv/bin/activate  # Linux/Mac
# или
venv\Scripts\activate     # Windows

# Установка зависимостей
pip install -r requirements.txt

# Копирование конфигурации
cp .env.example .env
```

### 3. Настройка конфигурации
Отредактируйте файл `.env`:
```bash
# Основные настройки
YANDEX_BOT_DEBUG=true
YANDEX_BOT_BROWSER_HEADLESS=false

# API ключ для антикапчи (обязательно!)
YANDEX_BOT_ANTICAPTCHA_API_KEY=your_api_key_here

# База данных (SQLite по умолчанию)
YANDEX_BOT_DATABASE_URL=sqlite:///./yandex_maps_bot.db
```

### 4. Локальная разработка

#### Вариант A: Ручной запуск компонентов
```bash
# Терминал 1: Запуск Redis
redis-server

# Терминал 2: Запуск базы данных (создание таблиц)
python -c "from app.database import create_tables; create_tables()"

# Терминал 3: Запуск Celery воркера
celery -A tasks.celery_app worker --loglevel=info

# Терминал 4: Запуск Celery Beat (планировщик)
celery -A tasks.celery_app beat --loglevel=info

# Терминал 5: Запуск веб-приложения
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

#### Вариант B: Docker Compose (рекомендуется)
```bash
# Запуск всей системы одной командой
docker-compose up --build

# Или в фоновом режиме
docker-compose up -d --build

# Просмотр логов
docker-compose logs -f

# Остановка всех сервисов
docker-compose down
```

### 5. Доступ к интерфейсам
- **Веб-интерфейс**: http://localhost:8000
- **API документация**: http://localhost:8000/docs
- **Celery мониторинг (Flower)**: http://localhost:5555
- **Grafana (мониторинг)**: http://localhost:3000 (admin/admin123)
- **Prometheus**: http://localhost:9090

### 6. Первоначальная настройка

#### 6.1 Добавление прокси серверов
```bash
# Через веб-интерфейс: http://localhost:8000/proxies
# Или через API:
curl -X POST http://localhost:8000/api/proxies \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Proxy 1",
    "host": "proxy.example.com",
    "port": 8080,
    "username": "user",
    "password": "pass",
    "proxy_type": "http"
  }'
```

#### 6.2 Создание профилей браузера
```bash
# Через веб-интерфейс: http://localhost:8000/profiles
# Или через API:
curl -X POST http://localhost:8000/api/profiles \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Profile 1",
    "viewport_width": 1366,
    "viewport_height": 768,
    "timezone": "Europe/Moscow",
    "language": "ru-RU"
  }'
```

#### 6.3 Прогрев профилей
```bash
# Запуск прогрева через API:
curl -X POST http://localhost:8000/api/profiles/1/start-warmup
```

### 7. Использование системы

#### 7.1 Базовый workflow
1. **Создайте профили браузера** в веб-интерфейсе
2. **Добавьте прокси серверы** для ротации IP
3. **Запустите прогрев профилей** (минимум 30 минут)
4. **Добавьте ссылки на профили Яндекс Карт**
5. **Запустите задачи посещения**

#### 7.2 Посещение профилей Яндекс Карт
```bash
# Через API:
curl -X POST http://localhost:8000/api/tasks/yandex-visit \
  -H "Content-Type: application/json" \
  -d '{
    "profile_id": 1,
    "target_url": "https://yandex.ru/maps/org/example/123456789/"
  }'
```

#### 7.3 Мониторинг задач
```bash
# Получение списка задач:
curl http://localhost:8000/api/tasks

# Статистика системы:
curl http://localhost:8000/api/dashboard/stats
```

## ⚡ Основные возможности

- **Многопрофильность**: 50+ браузерных профилей с уникальными fingerprint
- **AI-персоны**: Генерация реалистичных персон через Google Gemini
- **Умный прогрев**: 18 000+ URL (Яндекс-экосистема, рунет, международные)
- **Яндекс Карты**: Автоматические визиты с имитацией поведения
- **Яндекс Поиск**: Клики из поисковой выдачи на целевые сайты
- **Парсер**: Сбор данных компаний с Яндекс Карт (batch по регионам)
- **Email-рассылки**: Персонализированные письма с ротацией SMTP
- **Защита**: Ротация прокси, решение SmartCaptcha/reCAPTCHA (2captcha, anti-captcha, Capsola)
- **Масштабируемость**: 5 Celery-воркеров, 14 периодических задач
- **Мониторинг**: Веб-интерфейс (12 страниц), Flower, Grafana, Prometheus
- **Автопланировщик**: Расписание визитов, watchdog очередей, cleanup зависших задач
- **Wordstat**: Аналитика частотности ключевых слов

## ⚠️ Важные предупреждения

- Соблюдение robots.txt и условий использования Яндекс
- Разумные интервалы между запросами (5-30 секунд)
- Максимум 100 посещений в день с одного IP
- Использование только для легитимных бизнес-целей
- Мониторинг блокировок и временные паузы при обнаружении

## 📈 Производительность системы

- **Профили**: 50+ одновременных браузерных профилей
- **Прогрев**: 15 параллельных сессий (celery_warmup, conc=15)
- **Визиты Карт**: 2 параллельных визита (celery_yandex_maps, conc=2)
- **Клики Поиска**: 6 параллельных сессий (celery_yandex_search, conc=6)
- **Парсинг**: 2 параллельных задачи (celery_parser, conc=2)
- **Периодические задачи**: 14 автоматических через Celery Beat
- **URL прогрева**: 18 081 уникальных URL, ~3000+ доменов
- **Успешность**: 95%+ прохождения защит

## 🔒 Безопасность

Система включает механизмы для безопасной и легальной автоматизации:
- Соблюдение rate limits
- Уважение к robots.txt
- Использование только публичных данных
- Эмуляция естественного поведения пользователя

## 🔧 Конфигурация и настройка

### Основные настройки в .env файле

#### Антикапча сервисы
```bash
# 2captcha.com
YANDEX_BOT_ANTICAPTCHA_SERVICE=2captcha
YANDEX_BOT_ANTICAPTCHA_API_KEY=your_2captcha_key

# anti-captcha.com
YANDEX_BOT_ANTICAPTCHA_SERVICE=anticaptcha
YANDEX_BOT_ANTICAPTCHA_API_KEY=your_anticaptcha_key

# Capsola Cloud (SmartCaptcha / PazlCaptcha)
# Настраивается через user_settings в БД или скрипт:
# python scripts/insert_capsola_settings.py
```

#### AI и аналитика
```bash
# Google Gemini (для генерации AI-персон)
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
VERTEX_PROJECT=your-project-id
VERTEX_LOCATION=us-central1
# Или через API key:
GEMINI_API_KEY=your_gemini_key

# Yandex Wordstat API (частотность ключевых слов)
YANDEX_SEARCH_API_KEY=your_api_key
YANDEX_SEARCH_FOLDER_ID=your_folder_id
```

#### Настройки браузера
```bash
# Headless режим (без GUI)
YANDEX_BOT_BROWSER_HEADLESS=true

# Максимум одновременных браузеров
YANDEX_BOT_MAX_BROWSER_INSTANCES=5

# Таймауты
YANDEX_BOT_BROWSER_TIMEOUT=30

# Сохранение скриншотов визитов
YANDEX_BOT_SAVE_SCREENSHOTS=false

# Быстрый режим (сокращение задержек в 2 раза)
YANDEX_BOT_FAST_MODE=true
```

#### Настройки Celery
```bash
# Количество параллельных воркеров
CELERY_WORKER_CONCURRENCY=4
```

#### Настройки прогрева
```bash
# Длительность прогрева (минуты)
YANDEX_BOT_WARMUP_DURATION_MINUTES=30

# Время на странице (секунды)
YANDEX_BOT_WARMUP_MIN_PAGE_TIME=30
YANDEX_BOT_WARMUP_MAX_PAGE_TIME=300
```

#### Настройки Яндекс Карт
```bash
# Время посещения профиля (секунды)
YANDEX_BOT_YANDEX_MIN_VISIT_TIME=120
YANDEX_BOT_YANDEX_MAX_VISIT_TIME=600

# Задержки между запросами
YANDEX_BOT_MIN_REQUEST_DELAY=5
YANDEX_BOT_MAX_REQUEST_DELAY=30
```

## 🎯 Рекомендации по использованию

### Безопасное использование
1. **Умеренность**: Не более 100 посещений в день с одного IP
2. **Ротация**: Используйте разные прокси и профили
3. **Задержки**: Соблюдайте интервалы между запросами
4. **Прогрев**: Обязательно прогревайте профили перед использованием

### Оптимизация производительности
1. **Прокси**: Используйте быстрые и стабильные прокси серверы
2. **Headless режим**: Включите для экономии ресурсов в продакшн
3. **Мониторинг**: Следите за логами и метриками
4. **Масштабирование**: Добавляйте Celery воркеры при необходимости

### Типичные проблемы и решения

#### "Profile not ready for tasks"
**Проблема**: Профиль не прошел прогрев
**Решение**:
```bash
# Проверить статус профиля
curl http://localhost:8000/api/profiles/1

# Запустить прогрев
curl -X POST http://localhost:8000/api/profiles/1/start-warmup
```

#### "No available proxies"
**Проблема**: Все прокси недоступны или заблокированы
**Решение**:
```bash
# Проверить статус прокси
curl http://localhost:8000/api/proxies/stats

# Добавить новые прокси
curl -X POST http://localhost:8000/api/proxies -d '{"host":"...", "port":...}'
```

#### "Captcha detection timeout"
**Проблема**: Не удается решить капчу
**Решение**:
- Проверьте баланс антикапча сервиса
- Убедитесь в правильности API ключа
- Попробуйте другой сервис (2captcha ↔ anti-captcha)

#### "Browser session failed to start"
**Проблема**: Не удается запустить браузер
**Решение**:
- Проверьте установку Chrome/Chromium
- Убедитесь в наличии прав доступа
- В Docker: проверьте переменную `--no-sandbox`

## 📊 API документация

### Основные эндпоинты

#### Профили браузера
- `GET /api/profiles` — список профилей
- `POST /api/profiles` — создание профиля
- `GET /api/profiles/{id}` — информация о профиле
- `POST /api/profiles/{id}/start-warmup` — запуск прогрева
- `POST /api/profiles-bulk-create` — массовое создание профилей
- `POST /api/profiles-bulk-warmup` — запуск прогрева всех профилей
- `GET /api/profiles-overall-progress` — общий прогресс прогрева
- `GET /api/profiles-warmup-progress` — детали прогрева
- `DELETE /api/profiles-clear-all` — удаление всех профилей

#### Прокси серверы
- `GET /api/proxies` — список прокси
- `POST /api/proxies` — добавление прокси
- `POST /api/proxies/{id}/test` — тестирование прокси
- `GET /api/proxies/stats` — статистика прокси

#### Задачи
- `GET /api/tasks` — список задач
- `POST /api/tasks/warmup` — создание задачи прогрева
- `POST /api/tasks/yandex-visit` — создание задачи посещения Карт

#### Таргеты Яндекс Карт
- `GET /api/yandex-targets` — список таргетов
- `POST /api/yandex-targets` — добавление таргета (URL + расписание)
- `PUT /api/yandex-targets/{id}` — обновление таргета
- `DELETE /api/yandex-targets/{id}` — удаление

#### Таргеты Яндекс Поиска
- `GET /api/yandex-search-targets` — список таргетов Поиска
- `POST /api/yandex-search-targets` — добавление (домен + ключевые слова)
- `PUT /api/yandex-search-targets/{id}` — обновление
- `DELETE /api/yandex-search-targets/{id}` — удаление

#### Парсер Яндекс Карт
- `GET /api/parser/tasks` — список задач парсинга
- `POST /api/parser/tasks` — создать задачу (query + регион + max_items)
- `POST /api/parser/tasks/batch` — batch по списку регионов

#### Email-рассылки
- `GET /api/mailing/smtp-accounts` — список SMTP-аккаунтов
- `POST /api/mailing/smtp-accounts` — создать/обновить аккаунт
- `DELETE /api/mailing/smtp-accounts/{id}` — удалить
- `POST /api/mailing/smtp-accounts/{id}/test` — тест подключения

#### URL прогрева
- `GET /api/warmup-urls/stats` — статистика URL базы

#### Мониторинг
- `GET /api/dashboard/stats` — общая статистика
- `GET /health` — проверка работоспособности
- `GET /api/system/info` — информация о системе

### Примеры использования API

#### Полный цикл работы с профилем
```bash
# 1. Создание профиля
PROFILE_ID=$(curl -s -X POST http://localhost:8000/api/profiles \
  -H "Content-Type: application/json" \
  -d '{"name": "TestProfile", "timezone": "Europe/Moscow"}' \
  | jq -r '.id')

# 2. Прогрев профиля
curl -X POST http://localhost:8000/api/profiles/$PROFILE_ID/start-warmup

# 3. Ожидание завершения прогрева (проверка статуса)
while true; do
  STATUS=$(curl -s http://localhost:8000/api/profiles/$PROFILE_ID | jq -r '.warmup_completed')
  if [ "$STATUS" = "true" ]; then
    echo "Профиль прогрет!"
    break
  fi
  echo "Ожидание прогрева..."
  sleep 30
done

# 4. Запуск посещения Яндекс профиля
curl -X POST http://localhost:8000/api/tasks/yandex-visit \
  -H "Content-Type: application/json" \
  -d '{
    "profile_id": '$PROFILE_ID',
    "target_url": "https://yandex.ru/maps/org/example/123456789/"
  }'
```

## 🐳 Docker управление

### Полезные Docker команды
```bash
# Пересборка конкретного сервиса
docker-compose build app

# Просмотр логов конкретного сервиса
docker-compose logs -f celery_worker

# Вход в контейнер для отладки
docker-compose exec app bash

# Очистка данных
docker-compose down -v

# Мониторинг ресурсов
docker stats
```

### Масштабирование воркеров
```bash
# Увеличение количества воркеров для конкретной очереди
docker-compose up --scale celery_yandex_search=3

# Воркеры и их очереди в docker-compose.yml:
# celery_warmup           — warmup, proxy, maintenance (conc=15)
# celery_yandex_maps      — yandex_maps (conc=2)
# celery_yandex_search    — yandex_search (conc=6)
# celery_parser           — parser (conc=2)
```

## 📞 Поддержка и устранение неисправностей

### Диагностика проблем
1. **Проверьте логи** в веб-интерфейсе или Docker
2. **Убедитесь в доступности** Redis и базы данных
3. **Проверьте прокси** на работоспособность
4. **Проверьте баланс** антикапча сервиса
5. **Мониторинг ресурсов** - CPU, RAM, диск

### Логи системы
```bash
# Просмотр всех логов
docker-compose logs

# Логи конкретного сервиса
docker-compose logs app
docker-compose logs celery_warmup
docker-compose logs celery_yandex_maps
docker-compose logs celery_yandex_search
docker-compose logs celery_parser

# Следить за логами в реальном времени
docker-compose logs -f --tail=100
```

### Очистка и обслуживание
```bash
# Очистка старых задач
curl -X POST http://localhost:8000/api/maintenance/cleanup-old-tasks

# Сброс заблокированных прокси
curl -X POST http://localhost:8000/api/maintenance/reset-proxy-bans

# Обновление статистики
curl -X POST http://localhost:8000/api/maintenance/update-stats
```

### Контакты
- **Документация API**: http://localhost:8000/docs
- **Мониторинг задач**: http://localhost:5555 (Flower)
- **Метрики системы**: http://localhost:3000 (Grafana)

## 🔧 Утилиты и скрипты (scripts/)

| Скрипт | Описание |
|--------|----------|
| `status_check.sh` | Полная проверка: API health, Celery, Redis, DB |
| `check_web.sh` | Быстрый health check FastAPI |
| `cleanup_chrome.sh` | Убийство зависших Chrome/ChromeDriver процессов |
| `monitor_chrome.py` | Мониторинг Chrome-процессов во время прогрева |
| `warmup_all_profiles.py` | Прогрев всех непрогретых профилей |
| `import_warmup_urls.py` | Импорт URL из nagul.txt в БД |
| `extract_domains.py` | Извлечение уникальных доменов из nagul.txt |
| `fix_user_agents.py` | Обновление UA всех профилей под текущий Chrome |
| `reset_and_start.py` | Сброс застрявших профилей + быстрый прогрев |
| `run_scheduler_now.py` | Ручной запуск планировщика визитов |
| `insert_capsola_settings.py` | Вставка настроек Capsola в БД |
| `visit_yandex.py` | Ручной визит на Яндекс Карты через профиль |

## 🆕 Последние обновления (Март 2026)

### ✅ Исправление системы прогрева профилей

#### Проблемы, которые были решены:
- **"WARM all отдает No profiles available for warmup"** - исправлены конфликты URL роутинга в FastAPI
- **"статус Warmup пишет Pending почему не идет прогрев"** - восстановлена функциональность прогрева
- **"нагул кук"** - теперь cookie сохраняются корректно в browser_profiles

#### 🔧 Технические изменения:

##### 1. Исправлены конфликты URL роутинга в FastAPI
```diff
- /api/profiles/overall-progress   ❌ (конфликт с {profile_id})
+ /api/profiles-overall-progress   ✅ (исправлено)

- /api/profiles/bulk-create        ❌ (конфликт с {profile_id})
+ /api/profiles-bulk-create        ✅ (исправлено)

- /api/profiles/bulk-warmup        ❌ (конфликт с {profile_id})
+ /api/profiles-bulk-warmup        ✅ (исправлено)

- /api/profiles/warmup-progress    ❌ (конфликт с {profile_id})
+ /api/profiles-warmup-progress    ✅ (исправлено)
```

##### 2. Новая система управления URL для прогрева
```python
# Старая система (hardcoded сайты)
WARMUP_SITES = [
    'https://google.com',
    'https://youtube.com',
    # ... только 7 сайтов
]

# Новая система (база данных)
# 📊 Импортировано 18,081 уникальных URL из nagul.txt
# 🎯 3 стратегии выбора: diverse, random, popular
# 📈 Статистика использования URL
```

**Новые файлы:**
- `core/warmup_url_manager.py` - Менеджер URL с продвинутыми стратегиями
- `app/models/warmup_url.py` - Модель для хранения URL в БД
- `import_warmup_urls.py` - Скрипт импорта URL из nagul.txt

**Возможности WarmupUrlManager:**
```python
# Случайные URL
warmup_url_manager.get_random_urls(count=10, profile_id=1)

# Разнообразные URL (из разных доменов)
warmup_url_manager.get_diverse_urls(count=10, min_domains=5)

# URL по доменам
warmup_url_manager.get_urls_by_domain(['google.com'], max_per_domain=2)

# Популярные домены
warmup_url_manager.get_popular_domains(limit=50)

# Статистика
warmup_url_manager.get_statistics()
```

##### 3. Установка недостающих зависимостей
```bash
# Ранее отсутствовали:
pip install celery undetected-chromedriver

# Redis (macOS)
brew install redis
brew services start redis
```

##### 4. Исправления Celery и SQLAlchemy
```python
# Исправлен DetachedInstanceError
# Раньше: ❌
profile.some_field = value  # Объект уже detached от сессии

# Сейчас: ✅
with db_session() as db:
    profile = db.query(BrowserProfile).filter(...).first()
    profile.some_field = value
    db.commit()
```

##### 5. Улучшенный веб-интерфейс

**Новая кнопка "Clear All":**
- 🛡️ Тройное подтверждение безопасности
- 📊 Показ количества удаляемых профилей
- 🗂️ Удаление файлов browser_profiles
- ⚡ Real-time прогресс-бар

**Исправленные API endpoints в frontend:**
```javascript
// Обновлены все ссылки в profiles.html:
fetch('/api/profiles-overall-progress')
fetch('/api/profiles-bulk-warmup', {method: 'POST'})
fetch('/api/profiles-bulk-create', {method: 'POST'})
```

#### 📈 Результаты исправлений

**До исправления:**
- ❌ "No profiles available for warmup"
- ❌ Статус прогрева: "Pending"
- ❌ Прогрев не запускается
- ❌ Cookie не сохраняются
- ❌ API endpoints возвращают 422 ошибки

**После исправления:**
- ✅ "Bulk warmup started successfully"
- ✅ Статус прогрева: "In Progress"
- ✅ Celery задачи выполняются: ForkPoolWorker-1 through 4
- ✅ Cookie сохраняются в `./browser_profiles/{profile_id}/`
- ✅ Все API endpoints работают корректно

#### 🚀 Как использовать обновленную систему

```bash
# 1. Проверить общий прогресс
curl http://127.0.0.1:8000/api/profiles-overall-progress

# 2. Запустить прогрев всех профилей
curl -X POST http://127.0.0.1:8000/api/profiles-bulk-warmup

# 3. Создать множественные профили с автоматическим прогревом
curl -X POST http://127.0.0.1:8000/api/profiles-bulk-create \
  -H "Content-Type: application/json" \
  -d '{"count": 5, "auto_start_warmup": true}'

# 4. Очистить все профили
curl -X DELETE http://127.0.0.1:8000/api/profiles-clear-all
```

#### 📊 Статистика URL базы данных

```sql
-- Всего URL в базе: 18,081
-- Уникальных доменов: ~3,000+
-- Стратегии выбора: 3 (diverse, random, popular)
-- Fallback URL: 15 (если БД недоступна)
```

**Топ домены в системе:**
- google.com, youtube.com, wikipedia.org
- github.com, stackoverflow.com, habr.com
- vk.com, mail.ru, yandex.ru, dzen.ru
- И тысячи других для разнообразного прогрева

#### 🔍 Диагностика и мониторинг

**Проверка работы прогрева:**
```bash
# Статус Celery воркеров
celery -A tasks.celery_app inspect active

# Логи прогрева в реальном времени
docker-compose logs -f celery_worker

# Проверка папок профилей
ls -la ./browser_profiles/

# Статистика URL
curl http://127.0.0.1:8000/api/warmup-urls/stats
```

**Индикаторы успешного прогрева:**
- ✅ `warming_profiles > 0` в overall-progress
- ✅ Celery логи: "Profile X warmup completed successfully"
- ✅ Папки в `./browser_profiles/profile_X/` с cookie файлами
- ✅ Статус профиля: `"warmed"` вместо `"created"`

---

**⚠️ ВАЖНО**: Используйте систему ответственно, соблюдая правила и условия использования Яндекс Карт. Автоматизация должна выполняться для легитимных бизнес-целей.