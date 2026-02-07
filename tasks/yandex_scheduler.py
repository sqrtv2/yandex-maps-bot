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
from tasks.yandex_maps import visit_yandex_maps_profile_task

logger = logging.getLogger(__name__)


@shared_task(name='tasks.yandex_maps.schedule_visits')
def schedule_yandex_visits():
    """
    Check all active targets and schedule visits based on their configuration.
    This task runs every 5 minutes and checks if any targets need visits.
    """
    logger.info("🔄 Starting Yandex Maps visit scheduler")
    
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
            profiles = db.query(BrowserProfile).filter(
                BrowserProfile.warmup_completed == True
            ).all()
            
            if not profiles:
                logger.warning("⚠️  No warmed profiles available")
                return {
                    'status': 'error',
                    'message': 'No warmed profiles available',
                    'scheduled': 0
                }
            
            logger.info(f"✅ Found {len(profiles)} warmed profiles")
            
            scheduled_count = 0
            current_time = datetime.utcnow()
            
            # Process each target
            for target in targets:
                try:
                    # Check if target needs a visit
                    should_visit, reason = target.should_visit_now(current_time)
                    
                    if not should_visit:
                        logger.debug(f"⏭️  Skipping {target.title}: {reason}")
                        continue
                    
                    # Calculate how many visits to schedule now
                    visits_to_schedule = target.get_visits_needed_now(current_time)
                    
                    if visits_to_schedule <= 0:
                        logger.debug(f"⏭️  No visits needed for {target.title}")
                        continue
                    
                    logger.info(f"📅 Scheduling {visits_to_schedule} visits for: {target.title}")
                    
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
                        len(profiles)
                    )
                    
                    for i in range(concurrent_visits):
                        # Select profile (rotate through available profiles)
                        if target.use_different_profiles:
                            profile = profiles[scheduled_count % len(profiles)]
                        else:
                            # Use random profile if not rotating
                            profile = random.choice(profiles)
                        
                        # Add small random delay between concurrent visits (0-10 seconds)
                        delay_seconds = random.randint(0, 10) if i > 0 else 0
                        
                        # Schedule the visit task
                        visit_yandex_maps_profile_task.apply_async(
                            args=[profile.id, target.url, visit_params],
                            countdown=delay_seconds,
                            queue='yandex'
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
                queue='yandex'
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
