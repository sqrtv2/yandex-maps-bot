"""Vital/brand Yandex Search task & scheduler.

Этот модуль регистрирует ОТДЕЛЬНУЮ Celery-задачу
``yandex_search_vital_click_task`` (очередь ``yandex_search_vital``) и
простой планировщик ``schedule_search_vital_visits``.

Логика клика по выдаче полностью переиспользует
``tasks.yandex_search.yandex_search_click_task`` через ``.run(...)``,
передавая ``is_vital=True``. При этом основной таск:

- грузит данные из таблицы ``yandex_search_vital_targets`` (модель
  :class:`YandexSearchVitalTarget`);
- НЕ ставит ``context.route()`` на блокировку аналитики;
- НЕ инжектит ``_ANALYTICS_KILL_JS`` в target-страницу;
- НЕ обрывает загрузку target-страницы.

В результате на target-сайте корректно загружается Яндекс.Метрика и
Google Analytics, и переход с органической выдачи Яндекса засчитывается
счётчиком сайта как обычный визит.

Таска предназначена для **витальных/брендовых** запросов, где сайт
обычно стоит на 1-й позиции и нам важно «накручивать» именно
метрические визиты, а не позиции в SERP.
"""
from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta
from typing import Dict, Optional

from celery import shared_task

from app.database import get_db_session
from app.models import BrowserProfile, Task
from app.models.yandex_search_vital_target import YandexSearchVitalTarget
from tasks.celery_app import BaseTask
from tasks.yandex_search import yandex_search_click_task

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Задача витального клика — тонкая обёртка над основной yandex_search_click_task
# ──────────────────────────────────────────────────────────────────────────
@shared_task(
    base=BaseTask,
    bind=True,
    name="tasks.yandex_search_vital.yandex_search_vital_click_task",
    queue="yandex_search_vital",
    max_retries=0,  # vital → ретраи делает основной таск; здесь 0
    soft_time_limit=780,
    time_limit=840,
)
def yandex_search_vital_click_task(
    self,
    profile_id: int,
    target_id: int,
    keyword: str,
    task_id: Optional[int] = None,
    search_params: Optional[Dict] = None,
):
    """Витальный клик по выдаче Яндекса с включённой аналитикой на target."""
    return yandex_search_click_task.run(
        profile_id,
        target_id,
        keyword,
        task_id=task_id,
        search_params=search_params,
        is_vital=True,
    )


# ──────────────────────────────────────────────────────────────────────────
# Планировщик витальных визитов
# ──────────────────────────────────────────────────────────────────────────
MAX_CONCURRENT_VITAL_TASKS = 20
VITAL_BUFFER_TARGET = 6


@shared_task(
    base=BaseTask,
    name="tasks.yandex_search_vital.schedule_search_vital_visits",
    queue="yandex_search_vital",
)
def schedule_search_vital_visits():
    """Распределяет витальные клики по свободным прогретым профилям.

    Запускается раз в минуту celery-beat. Логика:

    1. Проверяем длину очереди ``yandex_search_vital`` и количество
       активных Task в БД — не флудим.
    2. Чистим зависшие in_progress/pending задачи.
    3. Берём активные ``YandexSearchVitalTarget``, отсортированные по
       приоритету.
    4. Для каждого таргета:
       - проверяем ``min_interval_minutes`` относительно ``last_visit_at``;
       - выбираем случайный активный keyword;
       - выбираем случайный warmed профиль, не занятый сейчас vital-задачей;
       - создаём запись ``Task`` и отправляем
         ``yandex_search_vital_click_task.apply_async``.
    """
    sl = logging.getLogger(__name__ + ".scheduler")
    sl.info("🔄 Vital search scheduler started")

    # ── Распределённый lock через Redis ──
    r = None
    lock_key = "scheduler:schedule_search_vital_visits:lock"
    try:
        import redis as _redis
        from app.config import settings as _s
        r = _redis.Redis(host=_s.redis_host, port=_s.redis_port)
        if not r.set(lock_key, "1", nx=True, ex=45):
            sl.info("⏭️ Another vital scheduler is running, skip")
            return {"status": "skipped", "reason": "duplicate", "scheduled": 0}
    except Exception as e:
        sl.warning(f"Vital scheduler: cannot acquire lock: {e}")

    # ── Не флудим очередь ──
    try:
        if r is not None:
            qlen = r.llen("yandex_search_vital") or 0
            if qlen > 30:
                sl.info(f"⏭️ yandex_search_vital queue has {qlen} tasks, skip")
                return {"status": "skipped", "reason": f"queue_full ({qlen})", "scheduled": 0}
    except Exception as qe:
        sl.warning(f"Vital scheduler: cannot read queue length: {qe}")

    # ── Сброс зависших задач ──
    try:
        with get_db_session() as db:
            now = datetime.utcnow()
            in_progress_cutoff = now - timedelta(minutes=11)
            pending_cutoff = now - timedelta(minutes=15)
            from sqlalchemy import or_
            stale_in_progress = db.query(Task).filter(
                Task.task_type == "yandex_search_vital",
                Task.status == "in_progress",
                or_(
                    (Task.started_at.isnot(None)) & (Task.started_at < in_progress_cutoff),
                    (Task.started_at.is_(None)) & (Task.created_at < in_progress_cutoff),
                ),
            ).all()
            stale_pending = db.query(Task).filter(
                Task.task_type == "yandex_search_vital",
                Task.status == "pending",
                Task.created_at < pending_cutoff,
            ).all()
            stale = list(stale_in_progress) + list(stale_pending)
            if stale:
                for st in stale:
                    st.status = "failed"
                    st.error_message = st.error_message or "Vital task stuck (auto-cleanup)"
                    st.completed_at = now
                    try:
                        st.add_log("🧹 Vital auto-cleanup")
                    except Exception:
                        pass
                db.commit()
                sl.info(f"🧹 Cleaned {len(stale)} stale vital tasks")
    except Exception as ce:
        sl.warning(f"Vital scheduler cleanup failed: {ce}")

    # ── Считаем активные задачи ──
    try:
        with get_db_session() as db:
            active_count = db.query(Task).filter(
                Task.task_type == "yandex_search_vital",
                Task.status.in_(["in_progress", "pending"]),
            ).count()
            pending_count = db.query(Task).filter(
                Task.task_type == "yandex_search_vital",
                Task.status == "pending",
            ).count()
    except Exception as ace:
        sl.warning(f"Vital scheduler: cannot count active tasks: {ace}")
        active_count = 0
        pending_count = 0

    if active_count >= MAX_CONCURRENT_VITAL_TASKS:
        return {"status": "skipped", "reason": f"too_many_active ({active_count})", "scheduled": 0}
    if pending_count >= VITAL_BUFFER_TARGET:
        return {"status": "skipped", "reason": f"buffer_full ({pending_count})", "scheduled": 0}

    slots_available = min(
        VITAL_BUFFER_TARGET - pending_count,
        MAX_CONCURRENT_VITAL_TASKS - active_count,
    )
    sl.info(f"📊 vital slots_available={slots_available} (pending={pending_count}, active={active_count})")

    if slots_available <= 0:
        return {"status": "skipped", "reason": "no_slots", "scheduled": 0}

    scheduled = 0
    try:
        with get_db_session() as db:
            targets = db.query(YandexSearchVitalTarget).filter(
                YandexSearchVitalTarget.is_active == True
            ).order_by(YandexSearchVitalTarget.priority.desc()).all()

            if not targets:
                sl.info("ℹ️  No active vital targets")
                return {"status": "success", "message": "no targets", "scheduled": 0}

            # Прогретые свободные профили
            all_profile_ids = [
                row[0] for row in db.query(BrowserProfile.id).filter(
                    BrowserProfile.warmup_completed == True,
                    BrowserProfile.is_active == True,
                    BrowserProfile.status == "warmed",
                ).all()
            ]
            if not all_profile_ids:
                sl.warning("⚠️  No warmed profiles for vital")
                return {"status": "error", "message": "no warmed profiles", "scheduled": 0}

            busy_rows = db.query(Task.profile_id).filter(
                Task.task_type.in_(["yandex_search", "yandex_search_vital"]),
                Task.status.in_(["in_progress", "pending"]),
                Task.profile_id.isnot(None),
            ).distinct().all()
            busy_ids = {row[0] for row in busy_rows}
            free_ids = [pid for pid in all_profile_ids if pid not in busy_ids]
            sl.info(
                f"✅ vital: {len(all_profile_ids)} warmed, {len(busy_ids)} busy, "
                f"{len(free_ids)} free"
            )
            if not free_ids:
                return {"status": "skipped", "reason": "all_profiles_busy", "scheduled": 0}

            random.shuffle(free_ids)
            assigned_this_run: set[int] = set()
            current_time = datetime.utcnow()

            for target in targets:
                if scheduled >= slots_available:
                    break
                # Проверка интервала
                if target.last_visit_at:
                    elapsed_min = (current_time - target.last_visit_at).total_seconds() / 60
                    if elapsed_min < (target.min_interval_minutes or 30) - 0.5:
                        continue

                kws = target.get_active_keywords_list()
                if not kws:
                    sl.info(f"ℹ️ vital target {target.id} ({target.domain}): no active keywords")
                    continue
                keyword = random.choice(kws)

                # Свободный профиль
                profile_id = None
                for pid in free_ids:
                    if pid in assigned_this_run:
                        continue
                    profile_id = pid
                    break
                if profile_id is None:
                    sl.info("⏭️ vital: no free profiles left in this run")
                    break
                assigned_this_run.add(profile_id)

                # Запись Task
                task_record = Task(
                    name=f"Vital '{keyword}' → {target.domain}",
                    task_type="yandex_search_vital",
                    profile_id=profile_id,
                    status="pending",
                    target_url=f"https://yandex.ru/search/?text={keyword}",
                    parameters={
                        "target_id": target.id,
                        "domain": target.domain,
                        "keyword": keyword,
                        "is_vital": True,
                    },
                    priority="normal",
                    created_at=current_time,
                )
                db.add(task_record)
                db.commit()
                db.refresh(task_record)

                search_params = {
                    "max_search_pages": target.max_search_pages or 5,
                    "min_time_on_site": target.min_time_on_site or 30,
                    "max_time_on_site": target.max_time_on_site or 120,
                }
                try:
                    yandex_search_vital_click_task.apply_async(
                        args=[profile_id, target.id, keyword, task_record.id, search_params],
                        queue="yandex_search_vital",
                    )
                    scheduled += 1
                    sl.info(
                        f"📤 vital task #{task_record.id}: profile={profile_id}, "
                        f"target={target.id} ({target.domain}), kw='{keyword[:60]}'"
                    )
                except Exception as send_err:
                    sl.error(f"Failed to apply_async vital task: {send_err}")
                    task_record.status = "failed"
                    task_record.error_message = f"apply_async failed: {send_err}"
                    db.commit()
    except Exception as e:
        sl.exception(f"Vital scheduler error: {e}")
        return {"status": "error", "error": str(e), "scheduled": scheduled}

    return {"status": "success", "scheduled": scheduled}


# ──────────────────────────────────────────────────────────────────────────
# Ежедневный сброс статистики vital таргетов
# ──────────────────────────────────────────────────────────────────────────
@shared_task(
    base=BaseTask,
    name="tasks.yandex_search_vital.daily_search_vital_stats_reset",
    queue="yandex_search_vital",
)
def daily_search_vital_stats_reset():
    """Сбрасывает счётчики today_* у vital таргетов."""
    sl = logging.getLogger(__name__ + ".reset")
    try:
        with get_db_session() as db:
            now = datetime.utcnow()
            targets = db.query(YandexSearchVitalTarget).all()
            for t in targets:
                t.today_visits = 0
                t.today_successful = 0
                t.today_failed = 0
                t.stats_reset_date = now
            db.commit()
            sl.info(f"🔁 Reset today_* for {len(targets)} vital targets")
            return {"status": "success", "reset": len(targets)}
    except Exception as e:
        sl.exception(f"Vital daily reset failed: {e}")
        return {"status": "error", "error": str(e)}
