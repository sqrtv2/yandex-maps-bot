# Конфигурация проекта

## Настройка многопоточности Celery

### Основной параметр

**`CELERY_WORKER_CONCURRENCY`** - количество параллельных worker процессов для Celery.

**Расположение настройки:**
- Файл конфигурации: `app/config.py` → `celery_worker_concurrency`
- Переменная окружения: `CELERY_WORKER_CONCURRENCY`
- По умолчанию: `4`

### Как изменить количество потоков

#### Метод 1: Переменная окружения (рекомендуется)

```bash
# В .env файле
CELERY_WORKER_CONCURRENCY=4

# Или перед запуском
export CELERY_WORKER_CONCURRENCY=4
./start_celery.sh
```

#### Метод 2: Прямое указание в команде

```bash
CELERY_WORKER_CONCURRENCY=6 ./start_celery.sh
```

#### Метод 3: Изменить в app/config.py

```python
celery_worker_concurrency: int = Field(
    default=6,  # Измените это значение
    description="Number of parallel Celery worker processes"
)
```

### Рекомендуемые значения

| Concurrency | RAM     | Описание | Время прогрева 10 профилей |
|-------------|---------|----------|---------------------------|
| **1**       | 4GB+    | Последовательная обработка (самый стабильный) | ~50 минут |
| **2**       | 8GB+    | Легкий параллелизм | ~25 минут |
| **3**       | 8GB+    | Умеренный параллелизм | ~17 минут |
| **4** ⭐    | 16GB+   | **Рекомендуемое** (баланс скорости и стабильности) | **~13 минут** |
| **6**       | 16GB+   | Высокий параллелизм | ~9 минут |
| **8**       | 32GB+   | Максимальный параллелизм | ~7 минут |

⚠️ **Важно:** При увеличении concurrency:
- Увеличивается нагрузка на CPU и RAM
- Могут возникать конфликты при создании браузеров
- Рекомендуется не превышать количество CPU cores × 1.5

### Мониторинг

Проверить текущее количество воркеров:

```bash
# Процессы Celery
ps aux | grep celery | grep ForkPoolWorker | wc -l

# Запущенные браузеры
ps aux | grep "Google Chrome" | grep -v "Helper\|grep" | wc -l
```

### Производительность

**Пример:** Прогрев 10 профилей по 5 минут каждый

| Concurrency | Формула | Время |
|-------------|---------|-------|
| 1 | 10 × 5 мин = 50 мин | 50 минут |
| 2 | ⌈10/2⌉ × 5 мин = 25 мин | 25 минут |
| 4 | ⌈10/4⌉ × 5 мин = 13 мин | **13 минут** ⭐ |
| 8 | ⌈10/8⌉ × 5 мин = 7 мин | 7 минут |

### Проблемы и решения

#### Ошибка: "Can not connect to the Service"

**Причина:** Слишком много браузеров запускается одновременно

**Решение:**
1. Уменьшите concurrency до 2-3
2. Увеличена задержка в `core/browser_manager.py` (1-3 секунды)

#### Высокая нагрузка на систему

**Решение:**
1. Уменьшите concurrency
2. Включите headless режим: `YANDEX_BOT_BROWSER_HEADLESS=true`
3. Отключите загрузку изображений в профилях

#### Профили падают с ошибками

**Решение:**
1. Запустите с concurrency=1 для диагностики
2. Проверьте логи: `tail -f logs/celery.log`
3. Celery автоматически повторит упавшие задачи

### Примеры конфигураций

#### Для слабого компьютера (4-8GB RAM)
```bash
CELERY_WORKER_CONCURRENCY=1
YANDEX_BOT_BROWSER_HEADLESS=true
```

#### Для среднего компьютера (8-16GB RAM)
```bash
CELERY_WORKER_CONCURRENCY=3
YANDEX_BOT_BROWSER_HEADLESS=false
```

#### Для мощного компьютера (16GB+ RAM) - рекомендуемое
```bash
CELERY_WORKER_CONCURRENCY=4
YANDEX_BOT_BROWSER_HEADLESS=false
```

#### Для сервера (32GB+ RAM)
```bash
CELERY_WORKER_CONCURRENCY=8
YANDEX_BOT_BROWSER_HEADLESS=true
YANDEX_BOT_MAX_BROWSER_INSTANCES=20
```

## Другие важные настройки

### Прогрев профилей

```bash
# Длительность прогрева одного профиля (минуты)
YANDEX_BOT_WARMUP_DURATION_MINUTES=30

# Минимальное время на странице (секунды)
YANDEX_BOT_WARMUP_MIN_PAGE_TIME=30

# Максимальное время на странице (секунды)
YANDEX_BOT_WARMUP_MAX_PAGE_TIME=300

# Количество сайтов за сессию
YANDEX_BOT_WARMUP_SITES_PER_SESSION=15
```

### Браузер

```bash
# Headless режим (быстрее, меньше нагрузка)
YANDEX_BOT_BROWSER_HEADLESS=false

# Таймаут загрузки страницы (секунды)
YANDEX_BOT_BROWSER_TIMEOUT=30

# Максимум браузеров одновременно
YANDEX_BOT_MAX_BROWSER_INSTANCES=10
```

## Применение изменений

После изменения настроек:

1. **Перезапустите Celery:**
   ```bash
   pkill -9 -f "celery.*worker"
   ./start_celery.sh
   ```

2. **Перезапустите API (если изменили настройки API):**
   ```bash
   # Ctrl+C в терминале с uvicorn, затем:
   python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
   ```

3. **Проверьте что применилось:**
   ```bash
   python3 -c "from app.config import settings; print(f'Concurrency: {settings.celery_worker_concurrency}')"
   ```
