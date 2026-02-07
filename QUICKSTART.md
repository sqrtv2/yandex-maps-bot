# Quick Start Guide - Profile Warmup

## ✅ Многопоточный прогрев настроен!

### Что исправлено:
1. ✅ Celery запускается с `--concurrency=4` (4 параллельных браузера)
2. ✅ Добавлены задержки для предотвращения конфликтов
3. ✅ Прогрев теперь в **4 раза быстрее**!

## Быстрый старт

### 1. Запуск системы (3 терминала)

**Терминал 1 - Redis:**
```bash
redis-server
```

**Терминал 2 - FastAPI:**
```bash
cd /Users/sqrtv2/Project/PF
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

**Терминал 3 - Celery Worker (4 потока):**
```bash
cd /Users/sqrtv2/Project/PF
./start_celery.sh
```

### 2. Запуск прогрева всех профилей

**Через скрипт:**
```bash
python3 warmup_all_profiles.py
```

**Или напрямую через Python:**
```python
from tasks.warmup import warmup_profile_task

# Прогрев всех профилей параллельно (4 одновременно)
for profile_id in range(1, 11):
    warmup_profile_task.delay(profile_id, duration_minutes=5)
```

### 3. Мониторинг

**Логи в реальном времени:**
```bash
tail -f logs/celery.log
```

**Статус профилей:**
```bash
python3 -c "
from app.database import get_db_session
from app.models import BrowserProfile

with get_db_session() as db:
    warming = db.query(BrowserProfile).filter(BrowserProfile.status == 'warming_up').count()
    warmed = db.query(BrowserProfile).filter(BrowserProfile.warmup_completed == True).count()
    total = db.query(BrowserProfile).count()
    print(f'⚡ Прогрето: {warmed}/{total}, В процессе: {warming} (параллельно)')
"
```

**Web интерфейс:**
```
http://127.0.0.1:8000/profiles
```

## Производительность

- **Однопоточный режим**: ~40 минут для 8 профилей (5 мин × 8)
- **Многопоточный режим (4 потока)**: ~10 минут для 8 профилей (**4x быстрее!**)

## Текущее состояние

- ✅ Profile-1, 2, 3: прогреты  
- ⏳ Profile-4 до Profile-10: прогреваются ПАРАЛЛЕЛЬНО (4 одновременно)

## Сброс застрявших профилей

Если нужно сбросить статус профилей:
```python
python3 -c "
from app.database import get_db_session
from app.models import BrowserProfile

with get_db_session() as db:
    for p in db.query(BrowserProfile).filter(
        BrowserProfile.status.in_(['warming_up', 'error'])
    ).all():
        p.status = 'created'
    db.commit()
"
```

## Полезные команды

```bash
# Проверить Redis
redis-cli ping

# Проверить API
curl http://127.0.0.1:8000/health

# Остановить Celery
pkill -9 -f "celery.*worker"

# Очистить Redis
redis-cli FLUSHDB

# Проверить процессы Chrome
ps aux | grep "Google Chrome" | grep -v grep | wc -l
```
