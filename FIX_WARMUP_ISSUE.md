# ИСПРАВЛЕНИЕ ПРОБЛЕМЫ С ПРОГРЕВОМ ПРОФИЛЕЙ

## Проблема
- Профили застряли в статусе `warming_up`
- Браузер не запускался для прогрева
- Celery задачи получались, но не выполнялись

## Причина
Celery worker запускался БЕЗ указания очередей и слушал только `default` очередь, в то время как задачи прогрева отправлялись в очередь `warmup`.

## Решение

### 1. Правильный запуск Celery Worker

**Используйте скрипт `start_celery.sh`:**
```bash
./start_celery.sh
```

Или запускайте вручную с указанием ВСЕХ очередей:
```bash
python3 -m celery -A tasks.celery_app.celery_app worker \
    --loglevel=info \
    --concurrency=1 \
    --queues=default,warmup,yandex,proxy,maintenance \
    --logfile=logs/celery.log
```

**Важно:**
- `--concurrency=1` - обязательно! Предотвращает конфликты при открытии нескольких браузеров
- `--queues=default,warmup,yandex,proxy,maintenance` - все очереди должны быть указаны

### 2. Сброс застрявших профилей

Если профили застряли в статусе `warming_up`:
```python
python3 -c "
from app.database import get_db_session
from app.models import BrowserProfile

with get_db_session() as db:
    stuck = db.query(BrowserProfile).filter(
        BrowserProfile.status == 'warming_up'
    ).all()
    for p in stuck:
        p.status = 'created'
    db.commit()
    print(f'Сброшено {len(stuck)} профилей')
"
```

### 3. Запуск прогрева всех профилей

**Используйте скрипт `warmup_all_profiles.py`:**
```bash
python3 warmup_all_profiles.py
```

Или через API:
```bash
# Прогрев отдельного профиля
curl -X POST http://127.0.0.1:8000/api/profiles/2/start-warmup

# Bulk прогрев всех профилей
curl -X POST http://127.0.0.1:8000/api/profiles-bulk-warmup
```

### 4. Мониторинг прогрева

Проверка статуса:
```bash
# Логи Celery
tail -f logs/celery.log

# Статус профилей
python3 -c "
from app.database import get_db_session
from app.models import BrowserProfile

with get_db_session() as db:
    profiles = db.query(BrowserProfile).all()
    for p in profiles:
        status = '✅' if p.warmup_completed else '⏳' if p.status == 'warming_up' else '❌'
        print(f'{status} {p.name}: {p.status}')
"

# Web интерфейс
# Откройте http://127.0.0.1:8000/profiles
```

## Пошаговая инструкция запуска

1. **Запустить Redis** (если не запущен):
   ```bash
   redis-server
   ```

2. **Запустить FastAPI сервер**:
   ```bash
   python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
   ```

3. **Запустить Celery Worker** (в отдельном терминале):
   ```bash
   ./start_celery.sh
   ```

4. **Запустить прогрев профилей**:
   ```bash
   python3 warmup_all_profiles.py
   ```

5. **Мониторинг**:
   - Web UI: http://127.0.0.1:8000/profiles
   - Логи: `tail -f logs/celery.log`

## Почему профили не прогревались

1. ❌ **Celery слушал только `default` очередь**
   - Решение: добавлен `--queues=default,warmup,yandex,proxy`

2. ❌ **Высокая concurrency вызывала конфликты браузеров**
   - Решение: установлен `--concurrency=1`

3. ❌ **Профили застревали в статусе `warming_up`**
   - Решение: добавлен скрипт для сброса статуса

4. ❌ **Нет папок для других профилей**
   - Это нормально! Папки создаются автоматически при первом прогреве
   - После прогрева вы увидите: `browser_profiles/Profile-2/`, `Profile-3/` и т.д.

## Проверка что все работает

```bash
# 1. Проверить Redis
redis-cli ping
# Должно вернуть: PONG

# 2. Проверить FastAPI
curl http://127.0.0.1:8000/health
# Должно вернуть: {"status":"healthy"}

# 3. Проверить Celery
ps aux | grep celery | grep -v grep
# Должны увидеть процесс celery worker

# 4. Проверить профили
python3 -c "from app.database import get_db_session; from app.models import BrowserProfile; db = next(get_db_session()); print(f'Профилей: {db.query(BrowserProfile).count()}')"
```

## Следующие шаги

После успешного прогрева:
- Профили будут в статусе `warmed`
- Появятся папки в `browser_profiles/` для каждого профиля
- Можно запускать визиты на Яндекс Карты через API
