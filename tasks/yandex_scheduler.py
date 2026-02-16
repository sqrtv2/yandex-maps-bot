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
from app.models import BrowserProfile
from app.models.task import Task
from app.models.profile_target_visit import ProfileTargetVisit
from tasks.yandex_maps import visit_yandex_maps_profile_task

logger = logging.getLogger(__name__)


@shared_task(name='tasks.yandex_maps.schedule_visits')
def schedule_yandex_visits():
    """
    Check all active targets and schedule visits based on their configuration.
    This task runs every 5 minutes and checks if any targets need visits.
    """
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
        queue_len = (r.llen('yandex_maps') or 0) + (r.llen('yandex') or 0)
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
                    
                    available_profiles = [p for p in all_profiles if p.id not in visited_profile_ids]
                    
                    if not available_profiles:
                        logger.warning(f"⚠️ All {len(all_profiles)} profiles already visited {target.title}, skipping")
                        continue
                    
                    logger.info(f"🔄 {len(available_profiles)} profiles available for {target.title} (visited: {len(visited_profile_ids)})")
                    
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
                        # Select profile from available (not yet visited) profiles
                        profile = available_profiles[i % len(available_profiles)]
                        
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
    Also resets profile_target_visits so profiles can visit targets again.
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
            
            # Reset profile-target visits so all profiles can visit again
            from app.models.profile_target_visit import ProfileTargetVisit as PTV
            deleted = db.query(PTV).delete()
            
            db.commit()
            
            logger.info(
                f"✅ Daily reset done: {len(targets)} targets zeroed, "
                f"{deleted} profile-visit records cleared"
            )
            
            return {
                'status': 'success',
                'targets_reset': len(targets),
                'visit_records_cleared': deleted,
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
    Удаляет профили, которые посетили ВСЕ активные цели.
    Удаляет: запись из БД (browser_profiles) + папку с диска.
    Запускается каждые 30 минут через celery beat.
    """
    import os
    import shutil
    from sqlalchemy import func
    from app.config import settings

    logger.info("🧹 Starting cleanup of fully-used profiles...")

    try:
        with get_db_session() as db:
            # Count active targets
            active_targets = db.query(YandexMapTarget).filter(
                YandexMapTarget.is_active == True
            ).all()
            active_target_ids = [t.id for t in active_targets]
            num_targets = len(active_target_ids)

            if num_targets == 0:
                logger.info("No active targets — nothing to clean up")
                return {'status': 'skipped', 'reason': 'no_active_targets'}

            # Find profiles that visited ALL active targets
            fully_used_subq = (
                db.query(ProfileTargetVisit.profile_id)
                .filter(ProfileTargetVisit.target_id.in_(active_target_ids))
                .group_by(ProfileTargetVisit.profile_id)
                .having(func.count(func.distinct(ProfileTargetVisit.target_id)) >= num_targets)
                .subquery()
            )

            fully_used_profiles = (
                db.query(BrowserProfile)
                .filter(BrowserProfile.id.in_(db.query(fully_used_subq.c.profile_id)))
                .all()
            )

            if not fully_used_profiles:
                logger.info(f"✅ No fully-used profiles to clean (targets: {num_targets})")
                return {
                    'status': 'success',
                    'deleted_profiles': 0,
                    'deleted_dirs': 0,
                    'active_targets': num_targets
                }

            deleted_profiles = 0
            deleted_dirs = 0
            errors = []

            for profile in fully_used_profiles:
                profile_name = profile.name
                profile_id = profile.id

                try:
                    # 1) Delete profile_target_visits records
                    db.query(ProfileTargetVisit).filter(
                        ProfileTargetVisit.profile_id == profile_id
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
                        logger.info(f"🗑️ Deleted profile {profile_name} (id={profile_id}) + disk folder")
                    else:
                        logger.info(f"🗑️ Deleted profile {profile_name} (id={profile_id}), no folder on disk")

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
                'active_targets': num_targets,
                'errors': errors if errors else None
            }
            return result

    except Exception as e:
        logger.error(f"❌ Cleanup error: {e}", exc_info=True)
        return {'status': 'error', 'error': str(e)}
