"""
Additional API routes for Yandex Maps Profile Visitor system.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import List, Dict, Any
import os
import logging
from datetime import datetime

from app.database import get_db
from app.models import BrowserProfile, ProxyServer, Task, UserSettings, YandexMapTarget, ProfileTargetVisit

logger = logging.getLogger(__name__)

# Setup templates
templates_path = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=templates_path) if os.path.exists(templates_path) else None

# Create router
router = APIRouter()

# Web Interface Routes (HTML pages)

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request, db: Session = Depends(get_db)):
    """Main dashboard page."""
    if not templates:
        return HTMLResponse("<h1>Templates not found</h1>")

    try:
        # Get basic stats for dashboard
        profile_count = db.query(BrowserProfile).count()
        proxy_count = db.query(ProxyServer).count()
        task_count = db.query(Task).count()

        context = {
            "request": request,
            "profile_count": profile_count,
            "proxy_count": proxy_count,
            "task_count": task_count
        }
        return templates.TemplateResponse("index.html", context)
    except Exception as e:
        logger.error(f"Error loading dashboard: {e}")
        return HTMLResponse("<h1>Error loading dashboard</h1>")


@router.get("/profiles", response_class=HTMLResponse)
async def profiles_page(request: Request):
    """Browser profiles management page."""
    if not templates:
        return HTMLResponse("<h1>Templates not found</h1>")

    return templates.TemplateResponse("profiles.html", {"request": request})


@router.get("/proxies", response_class=HTMLResponse)
async def proxies_page(request: Request):
    """Proxy servers management page."""
    if not templates:
        return HTMLResponse("<h1>Templates not found</h1>")

    return templates.TemplateResponse("proxies.html", {"request": request})


@router.get("/tasks", response_class=HTMLResponse)
async def tasks_page(request: Request):
    """Tasks management page."""
    if not templates:
        return HTMLResponse("<h1>Templates not found</h1>")

    return templates.TemplateResponse("tasks.html", {"request": request})


@router.get("/yandex-targets", response_class=HTMLResponse)
async def yandex_targets_page(request: Request):
    """Yandex Maps targets management page."""
    if not templates:
        return HTMLResponse("<h1>Templates not found</h1>")

    return templates.TemplateResponse("yandex_targets.html", {"request": request})


# Advanced API Routes

@router.get("/api/profiles/stats")
async def get_profile_stats(db: Session = Depends(get_db)):
    """Get detailed profile statistics."""
    try:
        profiles = db.query(BrowserProfile).all()

        stats = {
            "total": len(profiles),
            "active": sum(1 for p in profiles if p.is_active),
            "warmed": sum(1 for p in profiles if p.warmup_completed),
            "ready_for_tasks": sum(1 for p in profiles if p.is_ready_for_tasks()),
            "by_status": {}
        }

        # Count by status
        for profile in profiles:
            status = profile.status
            stats["by_status"][status] = stats["by_status"].get(status, 0) + 1

        # Calculate average success rates
        success_rates = [p.get_success_rate() for p in profiles if p.total_sessions > 0]
        stats["average_success_rate"] = sum(success_rates) / len(success_rates) if success_rates else 0

        return stats

    except Exception as e:
        logger.error(f"Error getting profile stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to get profile statistics")


@router.get("/api/proxies/stats")
async def get_proxy_stats(db: Session = Depends(get_db)):
    """Get detailed proxy statistics."""
    try:
        proxies = db.query(ProxyServer).all()

        stats = {
            "total": len(proxies),
            "working": sum(1 for p in proxies if p.is_working),
            "active": sum(1 for p in proxies if p.is_active),
            "available": sum(1 for p in proxies if p.is_available()),
            "by_status": {},
            "by_type": {},
            "by_country": {}
        }

        # Count by various categories
        for proxy in proxies:
            # By status
            status = proxy.status
            stats["by_status"][status] = stats["by_status"].get(status, 0) + 1

            # By type
            proxy_type = proxy.proxy_type
            stats["by_type"][proxy_type] = stats["by_type"].get(proxy_type, 0) + 1

            # By country
            if proxy.country:
                stats["by_country"][proxy.country] = stats["by_country"].get(proxy.country, 0) + 1

        # Calculate average response times and success rates
        response_times = [p.response_time_ms for p in proxies if p.response_time_ms]
        success_rates = [p.success_rate for p in proxies if p.total_requests > 0]

        stats["average_response_time"] = sum(response_times) / len(response_times) if response_times else 0
        stats["average_success_rate"] = sum(success_rates) / len(success_rates) if success_rates else 0

        return stats

    except Exception as e:
        logger.error(f"Error getting proxy stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to get proxy statistics")


@router.get("/api/tasks/stats")
async def get_task_stats(db: Session = Depends(get_db)):
    """Get detailed task statistics."""
    try:
        tasks = db.query(Task).all()

        stats = {
            "total": len(tasks),
            "by_status": {},
            "by_type": {},
            "by_priority": {}
        }

        # Count by various categories
        for task in tasks:
            # By status
            status = task.status
            stats["by_status"][status] = stats["by_status"].get(status, 0) + 1

            # By type
            task_type = task.task_type
            stats["by_type"][task_type] = stats["by_type"].get(task_type, 0) + 1

            # By priority
            priority = task.priority
            stats["by_priority"][priority] = stats["by_priority"].get(priority, 0) + 1

        # Calculate execution time statistics
        completed_tasks = [t for t in tasks if t.execution_time_seconds]
        if completed_tasks:
            execution_times = [t.execution_time_seconds for t in completed_tasks]
            stats["average_execution_time"] = sum(execution_times) / len(execution_times)
            stats["min_execution_time"] = min(execution_times)
            stats["max_execution_time"] = max(execution_times)
        else:
            stats["average_execution_time"] = 0
            stats["min_execution_time"] = 0
            stats["max_execution_time"] = 0

        return stats

    except Exception as e:
        logger.error(f"Error getting task stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to get task statistics")


@router.post("/api/profiles/{profile_id}/start-warmup")
async def start_profile_warmup(profile_id: int, db: Session = Depends(get_db)):
    """Start warmup for a specific profile."""
    try:
        profile = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")

        if profile.status == "warming_up":
            raise HTTPException(status_code=400, detail="Profile is already warming up")

        # Create warmup task record
        task = Task.create_warmup_task(profile_id=profile_id)
        db.add(task)

        # Update profile status
        profile.status = "warming_up"

        db.commit()

        # Dispatch actual Celery warmup task
        try:
            from tasks.warmup import warmup_profile_task
            celery_result = warmup_profile_task.delay(profile_id)
            logger.info(f"Dispatched warmup task for profile {profile_id}: {celery_result.id}")
        except Exception as e:
            logger.error(f"Failed to dispatch Celery warmup task: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to dispatch warmup task: {e}")

        return {"message": "Warmup started", "task_id": task.id}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting warmup for profile {profile_id}: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to start warmup")


@router.post("/api/profiles/{profile_id}/visit-yandex")
async def visit_yandex_profile(profile_id: int, visit_data: Dict[str, Any], db: Session = Depends(get_db)):
    """Create Yandex Maps visit task for a profile."""
    try:
        profile = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")

        if not profile.is_ready_for_tasks():
            raise HTTPException(
                status_code=400,
                detail="Profile is not ready for tasks. Complete warmup first."
            )

        target_url = visit_data.get("target_url")
        if not target_url:
            raise HTTPException(status_code=400, detail="target_url is required")

        # Validate Yandex Maps URL
        if "yandex" not in target_url.lower():
            raise HTTPException(status_code=400, detail="URL must be a Yandex Maps URL")

        # Create visit task
        task = Task.create_yandex_visit_task(
            profile_id=profile_id,
            target_url=target_url,
            parameters=visit_data.get("parameters", {})
        )
        db.add(task)
        db.commit()

        return {"message": "Yandex visit task created", "task_id": task.id}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating Yandex visit task for profile {profile_id}: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create visit task")


@router.post("/api/proxies/{proxy_id}/test")
async def test_proxy(proxy_id: int, db: Session = Depends(get_db)):
    """Test a proxy server connection."""
    try:
        proxy = db.query(ProxyServer).filter(ProxyServer.id == proxy_id).first()
        if not proxy:
            raise HTTPException(status_code=404, detail="Proxy not found")

        # Create health check task
        task = Task.create_health_check_task(proxy_id=proxy_id)
        db.add(task)
        db.commit()

        return {"message": "Proxy test started", "task_id": task.id}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error testing proxy {proxy_id}: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to test proxy")


@router.get("/api/settings/categories")
async def get_setting_categories(db: Session = Depends(get_db)):
    """Get all setting categories."""
    try:
        categories = db.query(UserSettings.category).distinct().all()
        return [cat[0] for cat in categories if cat[0]]
    except Exception as e:
        logger.error(f"Error getting setting categories: {e}")
        raise HTTPException(status_code=500, detail="Failed to get setting categories")


@router.get("/api/settings/category/{category}")
async def get_settings_by_category(category: str, db: Session = Depends(get_db)):
    """Get settings by category."""
    try:
        settings_list = db.query(UserSettings).filter(UserSettings.category == category).all()
        return [setting.to_dict() for setting in settings_list]
    except Exception as e:
        logger.error(f"Error getting settings for category {category}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get settings")


@router.post("/api/system/reset-database")
async def reset_database(confirmation: Dict[str, str]):
    """Reset the database (dangerous operation)."""
    if confirmation.get("confirm") != "YES_RESET_DATABASE":
        raise HTTPException(status_code=400, detail="Invalid confirmation")

    try:
        from app.database import db_manager
        db_manager.reset_database()
        return {"message": "Database reset successfully"}
    except Exception as e:
        logger.error(f"Error resetting database: {e}")
        raise HTTPException(status_code=500, detail="Failed to reset database")


@router.get("/api/system/info")
async def get_system_info():
    """Get system information."""
    try:
        from app.config import settings
        from app.database import db_manager

        db_info = db_manager.get_table_info()

        return {
            "app_name": settings.app_name,
            "app_version": settings.app_version,
            "database_url": settings.database_url,
            "redis_url": settings.redis_url,
            "database_tables": db_info,
            "settings": {
                "max_browser_instances": settings.max_browser_instances,
                "max_concurrent_tasks": settings.max_concurrent_tasks,
                "browser_headless": settings.browser_headless
            }
        }
    except Exception as e:
        logger.error(f"Error getting system info: {e}")
        raise HTTPException(status_code=500, detail="Failed to get system information")


# Yandex Maps Targets API Routes

@router.get("/api/yandex-targets")
async def get_yandex_targets(
    skip: int = 0,
    limit: int = 100,
    is_active: bool = None,
    db: Session = Depends(get_db)
):
    """Get all Yandex Maps target URLs."""
    try:
        query = db.query(YandexMapTarget)
        
        if is_active is not None:
            query = query.filter(YandexMapTarget.is_active == is_active)
        
        targets = query.offset(skip).limit(limit).all()
        return [target.to_dict() for target in targets]
    except Exception as e:
        logger.error(f"Error getting yandex targets: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/yandex-targets")
async def create_yandex_target(target_data: Dict[str, Any], db: Session = Depends(get_db)):
    """Create a new Yandex Maps target URL."""
    try:
        # Validate required fields
        if not target_data.get("url"):
            raise HTTPException(status_code=400, detail="URL is required")
        
        # Create new target
        target = YandexMapTarget(
            url=target_data["url"],
            title=target_data.get("title"),
            organization_name=target_data.get("organization_name"),
            visits_per_day=target_data.get("visits_per_day", 10),
            min_interval_minutes=target_data.get("min_interval_minutes", 60),
            max_interval_minutes=target_data.get("max_interval_minutes", 180),
            min_visit_duration=target_data.get("min_visit_duration", 120),
            max_visit_duration=target_data.get("max_visit_duration", 600),
            concurrent_visits=target_data.get("concurrent_visits", 1),
            use_different_profiles=target_data.get("use_different_profiles", True),
            priority=target_data.get("priority", 5),
            schedule_type=target_data.get("schedule_type", "distributed"),
            enabled_actions=target_data.get("enabled_actions", "scroll,photos,reviews,contacts,map"),
            notes=target_data.get("notes")
        )
        
        db.add(target)
        db.commit()
        db.refresh(target)
        
        return target.to_dict()
    except Exception as e:
        logger.error(f"Error creating yandex target: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/yandex-targets/{target_id}")
async def update_yandex_target(
    target_id: int,
    target_data: Dict[str, Any],
    db: Session = Depends(get_db)
):
    """Update a Yandex Maps target URL."""
    try:
        target = db.query(YandexMapTarget).filter(YandexMapTarget.id == target_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Target not found")
        
        # Update fields
        for key, value in target_data.items():
            if hasattr(target, key) and key not in ['id', 'created_at']:
                setattr(target, key, value)
        
        target.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(target)
        
        return target.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating yandex target: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/yandex-targets/{target_id}")
async def delete_yandex_target(target_id: int, db: Session = Depends(get_db)):
    """Delete a Yandex Maps target URL."""
    try:
        target = db.query(YandexMapTarget).filter(YandexMapTarget.id == target_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Target not found")
        
        db.delete(target)
        db.commit()
        
        return {"message": "Target deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting yandex target: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/yandex-targets/{target_id}/toggle")
async def toggle_yandex_target(target_id: int, db: Session = Depends(get_db)):
    """Toggle active status of a Yandex Maps target."""
    try:
        target = db.query(YandexMapTarget).filter(YandexMapTarget.id == target_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Target not found")
        
        target.is_active = not target.is_active
        target.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(target)
        
        return target.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error toggling yandex target: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/yandex-targets/{target_id}/visit-now")
async def visit_target_now(target_id: int, visit_params: Dict[str, Any] = None, db: Session = Depends(get_db)):
    """Start immediate visit to a Yandex Maps target with visual browser."""
    try:
        # Get target
        target = db.query(YandexMapTarget).filter(YandexMapTarget.id == target_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Target not found")
        
        # Get available warmed profiles
        profiles = db.query(BrowserProfile).filter(
            BrowserProfile.is_active == True,
            BrowserProfile.warmup_completed == True,
            BrowserProfile.status == "warmed"
        ).all()
        
        if not profiles:
            raise HTTPException(status_code=400, detail="No warmed profiles available. Please complete warmup first.")
        
        # Filter out profiles that already visited this target
        visited_profile_ids = db.query(ProfileTargetVisit.profile_id).filter(
            ProfileTargetVisit.target_id == target_id,
            ProfileTargetVisit.status == "completed"
        ).all()
        visited_ids = {row[0] for row in visited_profile_ids}
        
        available_profiles = [p for p in profiles if p.id not in visited_ids]
        
        if not available_profiles:
            raise HTTPException(
                status_code=400, 
                detail=f"Все профили уже посещали эту карту. Использовано {len(visited_ids)} из {len(profiles)} профилей."
            )
        
        # Select random profile from available ones
        import random
        profile = random.choice(available_profiles)
        
        # Create task parameters
        task_params = {
            'min_visit_time': target.min_visit_duration,
            'max_visit_time': target.max_visit_duration,
            'actions': target.enabled_actions.split(',') if target.enabled_actions else []
        }
        
        # Override with user params if provided
        if visit_params:
            task_params.update(visit_params)
        
        # Create visit task
        task = Task.create_yandex_visit_task(
            profile_id=profile.id,
            target_url=target.url,
            parameters=task_params
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        
        # Import and start task
        try:
            from tasks.yandex_maps import visit_yandex_maps_profile_task
            # Execute task asynchronously
            visit_yandex_maps_profile_task.delay(profile.id, target.url, task_params)
            
            # Update target stats
            target.last_visit_at = datetime.utcnow()
            db.commit()
            
            return {
                "message": "Visit started successfully",
                "task_id": task.id,
                "profile_id": profile.id,
                "profile_name": profile.name,
                "target_url": target.url
            }
        except ImportError:
            raise HTTPException(status_code=500, detail="Celery tasks not available")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting visit for target {target_id}: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/visit-logs")
async def get_visit_logs(limit: int = 30, db: Session = Depends(get_db)):
    """Get recent visit task logs for real-time progress display."""
    try:
        tasks = db.query(Task).filter(
            Task.task_type == "yandex_visit"
        ).order_by(Task.created_at.desc()).limit(limit).all()
        
        result = []
        for t in tasks:
            profile = None
            if t.profile_id:
                profile = db.query(BrowserProfile).filter(BrowserProfile.id == t.profile_id).first()
            
            result.append({
                "id": t.id,
                "status": t.status,
                "profile_id": t.profile_id,
                "profile_name": profile.name if profile else f"Profile-{t.profile_id}",
                "target_url": t.target_url or "",
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "started_at": t.started_at.isoformat() if t.started_at else None,
                "completed_at": t.completed_at.isoformat() if t.completed_at else None,
                "execution_time": t.execution_time_seconds,
                "error_message": t.error_message,
                "retry_count": t.retry_count,
                "logs": t.execution_logs or "",
                "result": t.result,
            })
        
        return result
    except Exception as e:
        logger.error(f"Error getting visit logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/yandex-targets/{target_id}/profile-visits")
async def get_target_profile_visits(target_id: int, db: Session = Depends(get_db)):
    """Get which profiles already visited this target."""
    try:
        visits = db.query(ProfileTargetVisit).filter(
            ProfileTargetVisit.target_id == target_id
        ).all()
        
        result = []
        for v in visits:
            profile = db.query(BrowserProfile).filter(BrowserProfile.id == v.profile_id).first()
            result.append({
                "profile_id": v.profile_id,
                "profile_name": profile.name if profile else f"Profile-{v.profile_id}",
                "status": v.status,
                "visited_at": v.visited_at.isoformat() if v.visited_at else None,
            })
        
        # Count available profiles
        total_warmed = db.query(BrowserProfile).filter(
            BrowserProfile.is_active == True,
            BrowserProfile.warmup_completed == True,
            BrowserProfile.status == "warmed"
        ).count()
        completed_count = sum(1 for v in visits if v.status == "completed")
        
        return {
            "target_id": target_id,
            "visits": result,
            "total_warmed_profiles": total_warmed,
            "used_profiles": completed_count,
            "available_profiles": total_warmed - completed_count,
        }
    except Exception as e:
        logger.error(f"Error getting profile visits for target {target_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/yandex-targets/{target_id}/reset-visits")
async def reset_target_visits(target_id: int, db: Session = Depends(get_db)):
    """Reset visit tracking for a target — all profiles can visit it again."""
    try:
        deleted = db.query(ProfileTargetVisit).filter(
            ProfileTargetVisit.target_id == target_id
        ).delete()
        db.commit()
        return {"message": f"Сброшено {deleted} записей. Все профили снова могут посещать эту карту."}
    except Exception as e:
        logger.error(f"Error resetting visits for target {target_id}: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
