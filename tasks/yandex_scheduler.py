"""
Scheduler for automatic Yandex Maps visits based on targets configuration.
"""
import logging
import random
from datetime import datetime, timedelta
from typing import List, Optional

from celery import shared_task

from app.database import get_db_session
from app.models.yandex_target import YandexMapTarget
from app.models.yandex_search_target import YandexSearchTarget
from app.models import BrowserProfile
from app.models.task import Task
from app.models.profile_target_visit import ProfileTargetVisit
from app.models.profile_search_visit import ProfileSearchVisit
from tasks.yandex_maps import visit_yandex_maps_profile_task

logger = logging.getLogger(__name__)


def _cleanup_stale_yandex_visit_tasks():
    """Clean up yandex_visit tasks stuck in 'in_progress' or 'pending' too long.
    
    Chrome/Celery workers killed by OOM (SIGKILL) leave tasks in 'in_progress'
    forever because the finally block never executes. This cleans them up.
    """
    try:
        with get_db_session() as db:
            now = datetime.utcnow()
            fixed = 0
            
            # In-progress yandex_visit tasks older than 7 minutes are stuck
            # (hard time_limit is 210s = 3.5 min, so 7 min is generous)
            stale_threshold = timedelta(minutes=7)
            stale_in_progress = db.query(Task).filter(
                Task.task_type == 'yandex_visit',
                Task.status == 'in_progress',
                Task.started_at.isnot(None),
                Task.started_at < (now - stale_threshold)
            ).all()
            
            for t in stale_in_progress:
                t.status = 'failed'
                t.error_message = t.error_message or 'Автоматически отменена: задача зависла (>7 мин, вероятно OOM-kill)'
                t.completed_at = now
                t.add_log('🧹 Автоочистка: задача зависла в in_progress (Chrome/worker убит OOM)')
                fixed += 1
            
            # Pending yandex_visit tasks older than 15 minutes are orphaned
            pending_threshold = timedelta(minutes=15)
            stale_pending = db.query(Task).filter(
                Task.task_type == 'yandex_visit',
                Task.status == 'pending',
                Task.created_at < (now - pending_threshold)
            ).all()
            
            for t in stale_pending:
                t.status = 'failed'
                t.error_message = 'Автоматически отменена: задача не запустилась (>15 мин)'
                t.completed_at = now
                t.add_log('🧹 Автоочистка: задача зависла в pending')
                fixed += 1
            
            if fixed:
                db.commit()
                logger.info(f"🧹 Cleaned up {fixed} stale yandex_visit tasks ({len(stale_in_progress)} in_progress, {len(stale_pending)} pending)")
    except Exception as e:
        logger.warning(f"Error cleaning up stale tasks: {e}")


@shared_task(name='tasks.yandex_maps.schedule_visits')
def schedule_yandex_visits():
    """
    Check all active targets and schedule visits based on their configuration.
    This task runs every 5 minutes and checks if any targets need visits.
    """
    # Clean up stale tasks before scheduling new ones
    _cleanup_stale_yandex_visit_tasks()
    
    # Distributed lock to prevent duplicate scheduler runs
    try:
        import redis as _redis
        from app.config import settings as _s
        r = _redis.Redis(host=_s.redis_host, port=_s.redis_port)
        lock_key = 'scheduler:schedule_visits:lock'
        if not r.set(lock_key, '1', nx=True, ex=240):  # 4 min lock (beat interval is 5 min)
            logger.info("⏭️ Another scheduler instance already running, skipping")
            return {'status': 'skipped', 'reason': 'duplicate', 'scheduled': 0}
    except Exception as le:
        logger.warning(f"Could not acquire scheduler lock: {le}")

    logger.info("🔄 Starting Yandex Maps visit scheduler")
    
    # Don't flood the queue — check how many tasks are already queued
    try:
        queue_len = r.llen('yandex_maps') or 0
        if queue_len > 20:
            logger.warning(f"⏭️ Yandex Maps queue already has {queue_len} tasks, skipping scheduling")
            return {'status': 'skipped', 'reason': f'queue_full ({queue_len})', 'scheduled': 0}
    except Exception as qe:
        logger.warning(f"Could not check queue length: {qe}")
    
    try:
        with get_db_session() as db:
            # Get all active targets
            targets = db.query(YandexMapTarget).filter(
                YandexMapTarget.is_active == True
            ).order_by(YandexMapTarget.priority.desc()).all()
            
            if not targets:
                logger.info("ℹ️  No active targets found")
                return {
                    'status': 'success',
                    'message': 'No active targets',
                    'scheduled': 0
                }
            
            logger.info(f"📊 Found {len(targets)} active targets")
            
            # Get available warmed profiles
            all_profiles = db.query(BrowserProfile).filter(
                BrowserProfile.warmup_completed == True
            ).all()
            
            if not all_profiles:
                logger.warning("⚠️  No warmed profiles available")
                return {
                    'status': 'error',
                    'message': 'No warmed profiles available',
                    'scheduled': 0
                }
            
            logger.info(f"✅ Found {len(all_profiles)} warmed profiles")
            
            scheduled_count = 0
            current_time = datetime.utcnow()
            
            # Track profiles used in THIS scheduling round to prevent
            # the same profile being assigned to multiple concurrent tasks
            # (two Chrome instances can't share the same user-data-dir)
            used_profile_ids_this_round = set()
            
            # Process each target
            for target in targets:
                try:
                    # Check if target needs a visit
                    should_visit, reason = target.should_visit_now(current_time)
                    
                    if not should_visit:
                        logger.info(f"⏭️  Skipping {target.title}: {reason}")
                        continue
                    
                    # Calculate how many visits to schedule now
                    visits_to_schedule = target.get_visits_needed_now(current_time)
                    
                    if visits_to_schedule <= 0:
                        logger.info(f"⏭️  No visits needed for {target.title}")
                        continue
                    
                    logger.info(f"📅 Scheduling {visits_to_schedule} visits for: {target.title}")
                    
                    # Filter out profiles that already visited this target
                    visited_profile_ids = set()
                    try:
                        visited_rows = db.query(ProfileTargetVisit.profile_id).filter(
                            ProfileTargetVisit.target_id == target.id,
                            ProfileTargetVisit.status == "completed"
                        ).all()
                        visited_profile_ids = {row[0] for row in visited_rows}
                    except Exception as ve:
                        logger.warning(f"Could not query visited profiles: {ve}")
                    
                    # Exclude profiles already visited AND profiles already scheduled this round
                    excluded_ids = visited_profile_ids | used_profile_ids_this_round
                    available_profiles = [p for p in all_profiles if p.id not in excluded_ids]
                    
                    if not available_profiles:
                        logger.warning(f"⚠️ No profiles available for {target.title} (visited: {len(visited_profile_ids)}, scheduled this round: {len(used_profile_ids_this_round)}), skipping")
                        continue
                    
                    logger.info(f"🔄 {len(available_profiles)} profiles available for {target.title} (visited: {len(visited_profile_ids)}, used this round: {len(used_profile_ids_this_round)})")
                    
                    # Prepare visit parameters from target configuration
                    visit_params = {
                        'min_visit_time': target.min_visit_duration,
                        'max_visit_time': target.max_visit_duration,
                        'actions': [],
                        'scroll_probability': 0.9 if target.is_action_enabled('scroll') else 0.0,
                        'photo_click_probability': 0.7 if target.is_action_enabled('photos') else 0.0,
                        'review_read_probability': 0.8 if target.is_action_enabled('reviews') else 0.0,
                        'contact_click_probability': 0.5 if target.is_action_enabled('contacts') else 0.0,
                        'map_interaction_probability': 0.6 if target.is_action_enabled('map') else 0.0,
                    }
                    
                    # Enable actions based on target configuration
                    if target.is_action_enabled('scroll'):
                        visit_params['actions'].append('scroll')
                    if target.is_action_enabled('photos'):
                        visit_params['actions'].append('view_photos')
                    if target.is_action_enabled('reviews'):
                        visit_params['actions'].append('read_reviews')
                    if target.is_action_enabled('contacts'):
                        visit_params['actions'].append('click_contacts')
                    if target.is_action_enabled('map'):
                        visit_params['actions'].append('view_map')
                    
                    # Schedule concurrent visits
                    concurrent_visits = min(
                        visits_to_schedule,
                        target.concurrent_visits,
                        len(available_profiles)
                    )
                    
                    # Shuffle available profiles so we pick different ones each time
                    random.shuffle(available_profiles)
                    
                    for i in range(concurrent_visits):
                        # Select profile from available (not yet visited, not used this round)
                        if i >= len(available_profiles):
                            logger.warning(f"⚠️ Not enough unique profiles for {target.title}, stopping at {i} visits")
                            break
                        profile = available_profiles[i]
                        
                        # Mark this profile as used so no other target picks it
                        used_profile_ids_this_round.add(profile.id)
                        
                        # Spread visits across the entire 5-minute window (0-280s)
                        # so they don't all start at once — looks more natural
                        delay_seconds = random.randint(0, 280)
                        
                        # Create Task record for UI visibility
                        task_record = Task(
                            name=f"Visit {target.title}",
                            task_type="yandex_visit",
                            status="pending",
                            target_url=target.url,
                            profile_id=profile.id,
                        )
                        db.add(task_record)
                        db.flush()  # get task_record.id
                        
                        # Schedule the visit task with task_id for log tracking
                        visit_yandex_maps_profile_task.apply_async(
                            args=[profile.id, target.url, visit_params],
                            kwargs={'task_id': task_record.id},
                            countdown=delay_seconds,
                            queue='yandex_maps'
                        )
                        
                        scheduled_count += 1
                        logger.info(
                            f"✅ Scheduled visit #{i+1}/{concurrent_visits} "
                            f"for {target.title} using profile {profile.id} "
                            f"(delay: {delay_seconds}s)"
                        )
                    
                    # Update target's last scheduled time (will be committed when task succeeds)
                    target.last_visit_at = current_time
                    db.commit()
                    
                except Exception as e:
                    logger.error(f"❌ Error scheduling visits for {target.title}: {e}", exc_info=True)
                    continue
            
            logger.info(f"✅ Scheduler completed. Scheduled {scheduled_count} visits")
            
            return {
                'status': 'success',
                'targets_processed': len(targets),
                'scheduled': scheduled_count,
                'timestamp': current_time.isoformat()
            }
            
    except Exception as e:
        logger.error(f"❌ Scheduler error: {e}", exc_info=True)
        return {
            'status': 'error',
            'error': str(e)
        }


@shared_task(name='tasks.yandex_maps.force_visit_target')
def force_visit_target(target_id: int, profile_id: Optional[int] = None):
    """
    Force immediate visit to a specific target, bypassing schedule.
    
    Args:
        target_id: Target ID to visit
        profile_id: Optional specific profile to use
    """
    logger.info(f"🚀 Force visiting target ID {target_id}")
    
    try:
        with get_db_session() as db:
            target = db.query(YandexMapTarget).filter(
                YandexMapTarget.id == target_id
            ).first()
            
            if not target:
                return {
                    'status': 'error',
                    'message': f'Target {target_id} not found'
                }
            
            # Get profile
            if profile_id:
                profile = db.query(BrowserProfile).filter(
                    BrowserProfile.id == profile_id
                ).first()
            else:
                # Get random warmed profile
                profile = db.query(BrowserProfile).filter(
                    BrowserProfile.warmup_completed == True
                ).first()
            
            if not profile:
                return {
                    'status': 'error',
                    'message': 'No suitable profile found'
                }
            
            # Prepare visit parameters
            visit_params = {
                'min_visit_time': target.min_visit_duration,
                'max_visit_time': target.max_visit_duration,
                'actions': [],
                'scroll_probability': 0.9 if target.is_action_enabled('scroll') else 0.0,
                'photo_click_probability': 0.7 if target.is_action_enabled('photos') else 0.0,
                'review_read_probability': 0.8 if target.is_action_enabled('reviews') else 0.0,
                'contact_click_probability': 0.5 if target.is_action_enabled('contacts') else 0.0,
                'map_interaction_probability': 0.6 if target.is_action_enabled('map') else 0.0,
            }
            
            # Enable actions
            if target.is_action_enabled('scroll'):
                visit_params['actions'].append('scroll')
            if target.is_action_enabled('photos'):
                visit_params['actions'].append('view_photos')
            if target.is_action_enabled('reviews'):
                visit_params['actions'].append('read_reviews')
            if target.is_action_enabled('contacts'):
                visit_params['actions'].append('click_contacts')
            if target.is_action_enabled('map'):
                visit_params['actions'].append('view_map')
            
            # Schedule immediate visit
            result = visit_yandex_maps_profile_task.apply_async(
                args=[profile.id, target.url, visit_params],
                queue='yandex_maps'
            )
            
            logger.info(f"✅ Forced visit scheduled: {target.title} with profile {profile.id}")
            
            return {
                'status': 'success',
                'target': target.title,
                'profile_id': profile.id,
                'task_id': result.id
            }
            
    except Exception as e:
        logger.error(f"❌ Force visit error: {e}", exc_info=True)
        return {
            'status': 'error',
            'error': str(e)
        }


@shared_task(name='tasks.yandex_maps.daily_stats_reset')
def daily_stats_reset():
    """
    Reset daily visit statistics for all targets.
    Runs at midnight UTC via celery beat.
    NOTE: profile_target_visits are NOT cleared — they are needed by
          cleanup_used_profiles to identify worked profiles.
          Profiles that visited a target will not be re-assigned to
          that same target (this is the intended behavior).
    """
    logger.info("🔄 Starting daily stats reset for Yandex targets")
    
    try:
        with get_db_session() as db:
            targets = db.query(YandexMapTarget).all()
            current_time = datetime.utcnow()
            
            for target in targets:
                target.today_visits = 0
                target.today_successful = 0
                target.today_failed = 0
                target.stats_reset_date = current_time
            
            db.commit()
            
            logger.info(
                f"✅ Daily reset done: {len(targets)} targets zeroed "
                f"(profile visit history preserved for cleanup)"
            )
            
            return {
                'status': 'success',
                'targets_reset': len(targets),
                'timestamp': current_time.isoformat()
            }
    except Exception as e:
        logger.error(f"❌ Daily stats reset error: {e}", exc_info=True)
        return {
            'status': 'error',
            'error': str(e)
        }


@shared_task(name='tasks.yandex_maps.cleanup_used_profiles')
def cleanup_used_profiles():
    """
    Удаляет отработанные профили.
    Профиль считается отработанным если:
      1) Посетил ВСЕ активные цели Яндекс Карт (fully used), ИЛИ
      2) Посетил ВСЕ активные цели Яндекс Поиска (fully used for search), ИЛИ
      3) Имеет визиты (карты или поиск), последний визит > STALE_HOURS назад.
    Удаляет: запись из БД (browser_profiles) + папку с диска.
    Запускается каждые 30 минут через celery beat.
    """
    import os
    import shutil
    from sqlalchemy import func
    from app.config import settings

    STALE_HOURS = 24  # профиль с визитами, последний визит > N часов назад → удалить

    logger.info("🧹 Starting cleanup of used profiles...")

    try:
        with get_db_session() as db:
            profiles_to_delete = []
            already_ids = set()

            # ── 1. Fully-used profiles for Maps (visited ALL active map targets) ──
            active_targets = db.query(YandexMapTarget).filter(
                YandexMapTarget.is_active == True
            ).all()
            active_target_ids = [t.id for t in active_targets]
            num_map_targets = len(active_target_ids)

            if num_map_targets > 0:
                fully_used_subq = (
                    db.query(ProfileTargetVisit.profile_id)
                    .filter(ProfileTargetVisit.target_id.in_(active_target_ids))
                    .group_by(ProfileTargetVisit.profile_id)
                    .having(func.count(func.distinct(ProfileTargetVisit.target_id)) >= num_map_targets)
                    .subquery()
                )

                fully_used = (
                    db.query(BrowserProfile)
                    .filter(BrowserProfile.id.in_(db.query(fully_used_subq.c.profile_id)))
                    .all()
                )
                for p in fully_used:
                    profiles_to_delete.append((p, 'fully_used_maps'))
                    already_ids.add(p.id)

                logger.info(f"Found {len(fully_used)} fully-used maps profiles (visited all {num_map_targets} targets)")

            # ── 2. Fully-used profiles for Search (visited ALL active search targets) ──
            active_search_targets = db.query(YandexSearchTarget).filter(
                YandexSearchTarget.is_active == True
            ).all()
            active_search_ids = [t.id for t in active_search_targets]
            num_search_targets = len(active_search_ids)

            if num_search_targets > 0:
                fully_used_search_subq = (
                    db.query(ProfileSearchVisit.profile_id)
                    .filter(ProfileSearchVisit.search_target_id.in_(active_search_ids))
                    .group_by(ProfileSearchVisit.profile_id)
                    .having(func.count(func.distinct(ProfileSearchVisit.search_target_id)) >= num_search_targets)
                    .subquery()
                )

                fully_used_search = (
                    db.query(BrowserProfile)
                    .filter(BrowserProfile.id.in_(db.query(fully_used_search_subq.c.profile_id)))
                    .all()
                )
                for p in fully_used_search:
                    if p.id not in already_ids:
                        profiles_to_delete.append((p, 'fully_used_search'))
                        already_ids.add(p.id)

                logger.info(f"Found {len(fully_used_search)} fully-used search profiles (visited all {num_search_targets} search targets)")

            # ── 3. Stale profiles: have completed visits (maps OR search), last visit > STALE_HOURS ago ──
            stale_cutoff = datetime.utcnow() - timedelta(hours=STALE_HOURS)

            # Stale map profiles
            stale_map_subq = (
                db.query(
                    ProfileTargetVisit.profile_id,
                    func.max(ProfileTargetVisit.visited_at).label('last_visit')
                )
                .filter(ProfileTargetVisit.status == "completed")
                .group_by(ProfileTargetVisit.profile_id)
                .having(func.max(ProfileTargetVisit.visited_at) < stale_cutoff)
                .subquery()
            )

            stale_map_ids = {r[0] for r in db.query(stale_map_subq.c.profile_id).all()}

            # Stale search profiles
            stale_search_subq = (
                db.query(
                    ProfileSearchVisit.profile_id,
                    func.max(ProfileSearchVisit.visited_at).label('last_visit')
                )
                .filter(ProfileSearchVisit.status == "completed")
                .group_by(ProfileSearchVisit.profile_id)
                .having(func.max(ProfileSearchVisit.visited_at) < stale_cutoff)
                .subquery()
            )

            stale_search_ids = {r[0] for r in db.query(stale_search_subq.c.profile_id).all()}

            stale_all_ids = stale_map_ids | stale_search_ids

            if stale_all_ids:
                stale_profiles = (
                    db.query(BrowserProfile)
                    .filter(
                        BrowserProfile.id.in_(stale_all_ids),
                        BrowserProfile.status.notin_(['warming_up']),
                    )
                    .all()
                )

                for p in stale_profiles:
                    if p.id not in already_ids:
                        profiles_to_delete.append((p, 'stale'))
                        already_ids.add(p.id)

                logger.info(
                    f"Found {len(stale_profiles)} stale profiles "
                    f"(have visits, last visit > {STALE_HOURS}h ago)"
                )
            else:
                logger.info(f"Found 0 stale profiles")

            if not profiles_to_delete:
                logger.info(f"✅ No profiles to clean up")
                return {
                    'status': 'success',
                    'deleted_profiles': 0,
                    'deleted_dirs': 0,
                    'active_map_targets': num_map_targets,
                    'active_search_targets': num_search_targets
                }

            deleted_profiles = 0
            deleted_dirs = 0
            errors = []

            for profile, reason in profiles_to_delete:
                profile_name = profile.name
                profile_id = profile.id

                try:
                    # 1) Delete profile_target_visits records (maps)
                    db.query(ProfileTargetVisit).filter(
                        ProfileTargetVisit.profile_id == profile_id
                    ).delete(synchronize_session=False)

                    # 1b) Delete profile_search_visits records (search)
                    db.query(ProfileSearchVisit).filter(
                        ProfileSearchVisit.profile_id == profile_id
                    ).delete(synchronize_session=False)

                    # 2) Nullify profile_id in tasks table (preserve task history)
                    db.query(Task).filter(
                        Task.profile_id == profile_id
                    ).update({Task.profile_id: None}, synchronize_session=False)

                    # 3) Delete profile from DB
                    db.delete(profile)
                    deleted_profiles += 1

                    # 4) Delete folder from disk
                    profile_dir = os.path.join(settings.browser_user_data_dir, profile_name)
                    if os.path.exists(profile_dir):
                        shutil.rmtree(profile_dir, ignore_errors=True)
                        deleted_dirs += 1
                        logger.info(f"🗑️ Deleted profile {profile_name} (id={profile_id}) [{reason}] + disk folder")
                    else:
                        logger.info(f"🗑️ Deleted profile {profile_name} (id={profile_id}) [{reason}], no folder on disk")

                except Exception as e:
                    errors.append(f"{profile_name}: {e}")
                    logger.warning(f"⚠️ Error deleting profile {profile_name}: {e}")

            db.commit()

            logger.info(
                f"🧹 Cleanup done: {deleted_profiles} profiles deleted from DB, "
                f"{deleted_dirs} directories removed from disk"
            )

            result = {
                'status': 'success',
                'deleted_profiles': deleted_profiles,
                'deleted_dirs': deleted_dirs,
                'active_map_targets': num_map_targets,
                'active_search_targets': num_search_targets,
                'errors': errors if errors else None
            }
            return result

    except Exception as e:
        logger.error(f"❌ Cleanup error: {e}", exc_info=True)
        return {'status': 'error', 'error': str(e)}


@shared_task(name='tasks.yandex_scheduler.queue_watchdog')
def queue_watchdog():
    """Watchdog that monitors Redis queues and DB task states.
    
    Prevents queue overflow by:
    1. Purging Redis queue if it exceeds the threshold
    2. Cleaning up orphaned pending/in_progress tasks in DB
    3. Logging queue health for monitoring
    
    Runs every 3 minutes via celery beat.
    """
    import redis as _redis
    from app.config import settings as _s

    QUEUE_MAX = 30  # Max tasks in Redis queue before purge
    PENDING_MAX_MINUTES = 30  # Max age for pending tasks
    IN_PROGRESS_MAX_MINUTES = 10  # Max age for in_progress tasks (must be > celery soft_time_limit=540s=9min)

    results = {}

    try:
        r = _redis.Redis(host=_s.redis_host, port=_s.redis_port)

        # --- 1. Check all queues ---
        queues_to_watch = ['yandex_maps', 'yandex_search', 'default', 'warmup']
        # Warmup tasks are slow (~25 min each) so large queue is normal — never purge
        queues_skip_purge = {'warmup'}
        for queue_name in queues_to_watch:
            qlen = r.llen(queue_name) or 0
            results[f'queue_{queue_name}'] = qlen

            if qlen > QUEUE_MAX and queue_name not in queues_skip_purge:
                logger.warning(
                    f"🚨 Queue '{queue_name}' overflow: {qlen} tasks (max {QUEUE_MAX}). Purging..."
                )
                r.delete(queue_name)
                results[f'purged_{queue_name}'] = qlen
                logger.info(f"✅ Purged queue '{queue_name}': {qlen} → 0")

        # --- 2. Clean up stale DB tasks for ALL task types ---
        with get_db_session() as db:
            now = datetime.utcnow()
            total_fixed = 0

            for task_type in ['yandex_visit', 'yandex_search', 'warmup']:
                # Stale in_progress
                stale_ip = db.query(Task).filter(
                    Task.task_type == task_type,
                    Task.status == 'in_progress',
                    Task.started_at.isnot(None),
                    Task.started_at < (now - timedelta(minutes=IN_PROGRESS_MAX_MINUTES))
                ).all()

                for t in stale_ip:
                    t.status = 'failed'
                    t.error_message = t.error_message or f'Watchdog: зависла в in_progress (>{IN_PROGRESS_MAX_MINUTES} мин)'
                    t.completed_at = now
                    t.add_log(f'🐕 Watchdog: задача зависла в in_progress')
                    total_fixed += 1

                # Stale pending
                stale_pending = db.query(Task).filter(
                    Task.task_type == task_type,
                    Task.status == 'pending',
                    Task.created_at < (now - timedelta(minutes=PENDING_MAX_MINUTES))
                ).all()

                for t in stale_pending:
                    t.status = 'failed'
                    t.error_message = f'Watchdog: не запустилась (>{PENDING_MAX_MINUTES} мин)'
                    t.completed_at = now
                    t.add_log(f'🐕 Watchdog: задача зависла в pending')
                    total_fixed += 1

                if stale_ip or stale_pending:
                    results[f'cleaned_{task_type}'] = {
                        'in_progress': len(stale_ip),
                        'pending': len(stale_pending)
                    }

            if total_fixed:
                db.commit()
                logger.info(f"🐕 Watchdog cleaned up {total_fixed} stale tasks")

            results['total_cleaned'] = total_fixed

        # --- 3. Log health summary ---
        queue_summary = ', '.join(f"{q}={results.get(f'queue_{q}', '?')}" for q in queues_to_watch)
        logger.info(f"🐕 Watchdog OK | Queues: {queue_summary} | Cleaned: {results.get('total_cleaned', 0)}")

        return results

    except Exception as e:
        logger.error(f"🐕 Watchdog error: {e}", exc_info=True)
        return {'status': 'error', 'error': str(e)}
