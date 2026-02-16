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
import subprocess
from datetime import datetime, timedelta

from app.database import get_db
from app.models import BrowserProfile, ProxyServer, Task, UserSettings, YandexMapTarget, ProfileTargetVisit
from app.models.yandex_search_target import YandexSearchTarget

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


@router.get("/yandex-search", response_class=HTMLResponse)
async def yandex_search_page(request: Request):
    """Yandex Search click-through targets management page."""
    if not templates:
        return HTMLResponse("<h1>Templates not found</h1>")

    return templates.TemplateResponse("yandex_search.html", {"request": request})


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Worker and system settings page."""
    if not templates:
        return HTMLResponse("<h1>Templates not found</h1>")

    return templates.TemplateResponse("settings.html", {"request": request})


# Worker Management API

@router.get("/api/workers/status")
async def get_workers_status(db: Session = Depends(get_db)):
    """Get status of all celery workers and queue lengths."""
    try:
        import redis as _redis
        from app.config import settings as _s
        r = _redis.Redis(host=_s.redis_host, port=_s.redis_port)
        
        # Get queue lengths
        queues = {}
        for q_name in ['warmup', 'yandex_maps', 'yandex_search', 'default', 'proxy', 'maintenance']:
            queues[q_name] = r.llen(q_name) or 0
        
        # Also check legacy 'yandex' queue
        legacy_yandex = r.llen('yandex') or 0
        if legacy_yandex > 0:
            queues['yandex_maps'] += legacy_yandex
        
        # Get worker info from celery inspect
        from tasks.celery_app import celery_app
        inspect = celery_app.control.inspect(timeout=5)
        
        active_tasks = {}
        worker_stats = {}
        
        try:
            active = inspect.active() or {}
            stats = inspect.stats() or {}
            
            for worker_name, tasks in active.items():
                active_tasks[worker_name] = len(tasks)
            
            for worker_name, st in stats.items():
                worker_stats[worker_name] = {
                    'concurrency': st.get('pool', {}).get('max-concurrency', 0),
                    'total_tasks': st.get('total', {})
                }
        except Exception as e:
            logger.warning(f"Could not inspect workers: {e}")
        
        # Get settings from DB
        worker_settings = {}
        settings_keys = [
            'worker_warmup_concurrency', 'worker_yandex_maps_concurrency', 'worker_yandex_search_concurrency',
            'worker_warmup_enabled', 'worker_yandex_maps_enabled', 'worker_yandex_search_enabled',
            'worker_warmup_memory_limit', 'worker_yandex_maps_memory_limit', 'worker_yandex_search_memory_limit'
        ]
        for key in settings_keys:
            setting = db.query(UserSettings).filter(UserSettings.setting_key == key).first()
            if setting:
                worker_settings[key] = setting.get_typed_value()
        
        return {
            'queues': queues,
            'active_tasks': active_tasks,
            'worker_stats': worker_stats,
            'settings': worker_settings
        }
    except Exception as e:
        logger.error(f"Error getting worker status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/workers/apply")
async def apply_worker_settings(data: Dict[str, Any], db: Session = Depends(get_db)):
    """Save worker settings and generate docker-compose restart command."""
    try:
        # Update settings in DB
        for key, value in data.items():
            setting = db.query(UserSettings).filter(UserSettings.setting_key == key).first()
            if setting:
                setting.set_typed_value(value)
            else:
                # Create new setting
                setting_type = 'int' if isinstance(value, int) else ('bool' if isinstance(value, bool) else 'string')
                new_setting = UserSettings(
                    setting_key=key,
                    setting_value=str(value),
                    setting_type=setting_type,
                    category='workers',
                    description=f'Worker setting: {key}'
                )
                db.add(new_setting)
        db.commit()
        
        # Read back the updated settings
        warmup_conc = int(data.get('worker_warmup_concurrency', 5))
        maps_conc = int(data.get('worker_yandex_maps_concurrency', 3))
        search_conc = int(data.get('worker_yandex_search_concurrency', 3))
        warmup_enabled = data.get('worker_warmup_enabled', True)
        maps_enabled = data.get('worker_yandex_maps_enabled', True)
        search_enabled = data.get('worker_yandex_search_enabled', True)
        warmup_mem = int(data.get('worker_warmup_memory_limit', 16))
        maps_mem = int(data.get('worker_yandex_maps_memory_limit', 8))
        search_mem = int(data.get('worker_yandex_search_memory_limit', 8))
        
        return {
            'status': 'saved',
            'message': 'Настройки сохранены. Для применения нужно перезапустить воркеры.',
            'settings': {
                'warmup': {'concurrency': warmup_conc, 'enabled': warmup_enabled, 'memory_gb': warmup_mem},
                'yandex_maps': {'concurrency': maps_conc, 'enabled': maps_enabled, 'memory_gb': maps_mem},
                'yandex_search': {'concurrency': search_conc, 'enabled': search_enabled, 'memory_gb': search_mem},
            }
        }
    except Exception as e:
        logger.error(f"Error applying worker settings: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/workers/restart")
async def restart_workers(db: Session = Depends(get_db)):
    """Restart celery workers with updated concurrency settings from DB."""
    try:
        # Read settings from DB
        def get_setting(key, default):
            s = db.query(UserSettings).filter(UserSettings.setting_key == key).first()
            return s.get_typed_value() if s else default
        
        warmup_conc = get_setting('worker_warmup_concurrency', 5)
        maps_conc = get_setting('worker_yandex_maps_concurrency', 3)
        search_conc = get_setting('worker_yandex_search_concurrency', 3)
        warmup_enabled = get_setting('worker_warmup_enabled', True)
        maps_enabled = get_setting('worker_yandex_maps_enabled', True)
        search_enabled = get_setting('worker_yandex_search_enabled', True)
        warmup_mem = get_setting('worker_warmup_memory_limit', 16)
        maps_mem = get_setting('worker_yandex_maps_memory_limit', 8)
        search_mem = get_setting('worker_yandex_search_memory_limit', 8)
        
        # Generate docker-compose override file dynamically
        import yaml
        
        compose_override = {'services': {}}
        
        if warmup_enabled:
            compose_override['services']['celery_warmup'] = {
                'build': '.',
                'init': True,
                'command': f'celery -A tasks.celery_app worker --loglevel=info --concurrency={warmup_conc} -Q default,warmup,proxy,maintenance -n warmup@%h',
                'shm_size': '2gb',
                'environment': [
                    'YANDEX_BOT_DATABASE_URL=postgresql://postgres:password@postgres:5432/yandex_maps_bot',
                    'YANDEX_BOT_REDIS_HOST=redis',
                    'YANDEX_BOT_REDIS_PORT=6379',
                    'YANDEX_BOT_DEBUG=false',
                    'YANDEX_BOT_BROWSER_HEADLESS=true',
                    'YANDEX_BOT_SAVE_SCREENSHOTS=false',
                    'YANDEX_BOT_FAST_MODE=true',
                ],
                'volumes': [
                    './data:/app/data', './logs:/app/logs', './browser_profiles:/app/browser_profiles',
                    './tasks:/app/tasks', './core:/app/core', './app:/app/app', './web:/app/web',
                ],
                'depends_on': ['postgres', 'redis'],
                'restart': 'unless-stopped',
                'deploy': {'resources': {'limits': {'memory': f'{warmup_mem}G'}}},
                'networks': ['yandex_maps_network'],
            }
        
        if maps_enabled:
            compose_override['services']['celery_yandex_maps'] = {
                'build': '.',
                'init': True,
                'command': f'celery -A tasks.celery_app worker --loglevel=info --concurrency={maps_conc} -Q yandex_maps -n yandex_maps@%h',
                'shm_size': '2gb',
                'environment': [
                    'YANDEX_BOT_DATABASE_URL=postgresql://postgres:password@postgres:5432/yandex_maps_bot',
                    'YANDEX_BOT_REDIS_HOST=redis',
                    'YANDEX_BOT_REDIS_PORT=6379',
                    'YANDEX_BOT_DEBUG=false',
                    'YANDEX_BOT_BROWSER_HEADLESS=true',
                    'YANDEX_BOT_SAVE_SCREENSHOTS=false',
                    'YANDEX_BOT_FAST_MODE=true',
                ],
                'volumes': [
                    './data:/app/data', './logs:/app/logs', './screenshots:/app/screenshots',
                    './browser_profiles:/app/browser_profiles',
                    './tasks:/app/tasks', './core:/app/core', './app:/app/app', './web:/app/web',
                ],
                'depends_on': ['postgres', 'redis'],
                'restart': 'unless-stopped',
                'deploy': {'resources': {'limits': {'memory': f'{maps_mem}G'}}},
                'networks': ['yandex_maps_network'],
            }
        
        if search_enabled:
            compose_override['services']['celery_yandex_search'] = {
                'build': '.',
                'init': True,
                'command': f'celery -A tasks.celery_app worker --loglevel=info --concurrency={search_conc} -Q yandex_search -n yandex_search@%h',
                'shm_size': '2gb',
                'environment': [
                    'YANDEX_BOT_DATABASE_URL=postgresql://postgres:password@postgres:5432/yandex_maps_bot',
                    'YANDEX_BOT_REDIS_HOST=redis',
                    'YANDEX_BOT_REDIS_PORT=6379',
                    'YANDEX_BOT_DEBUG=false',
                    'YANDEX_BOT_BROWSER_HEADLESS=true',
                    'YANDEX_BOT_SAVE_SCREENSHOTS=false',
                    'YANDEX_BOT_FAST_MODE=true',
                ],
                'volumes': [
                    './data:/app/data', './logs:/app/logs', './screenshots:/app/screenshots',
                    './browser_profiles:/app/browser_profiles',
                    './tasks:/app/tasks', './core:/app/core', './app:/app/app', './web:/app/web',
                ],
                'depends_on': ['postgres', 'redis'],
                'restart': 'unless-stopped',
                'deploy': {'resources': {'limits': {'memory': f'{search_mem}G'}}},
                'networks': ['yandex_maps_network'],
            }
        
        # Write the override file
        override_path = '/app/docker-compose.workers.yml'
        try:
            with open(override_path, 'w') as f:
                yaml.dump(compose_override, f, default_flow_style=False)
        except Exception:
            # Fallback path if /app is not writable
            override_path = './docker-compose.workers.yml'
            with open(override_path, 'w') as f:
                yaml.dump(compose_override, f, default_flow_style=False)
        
        # Execute restart via subprocess
        import subprocess
        
        # Stop old workers
        stop_cmds = [
            'docker compose stop celery_worker celery_yandex 2>/dev/null || true',
            'docker compose rm -f celery_worker celery_yandex 2>/dev/null || true',
        ]
        
        for cmd in stop_cmds:
            try:
                subprocess.run(cmd, shell=True, timeout=30, capture_output=True)
            except Exception as e:
                logger.warning(f"Stop command failed: {cmd}: {e}")

        # Start new workers using override
        start_cmd = f'docker compose -f docker-compose.yml -f {override_path} up -d celery_warmup celery_yandex_maps celery_yandex_search 2>&1'
        try:
            result = subprocess.run(start_cmd, shell=True, timeout=120, capture_output=True, text=True)
            output = result.stdout + result.stderr
        except Exception as e:
            output = str(e)
        
        return {
            'status': 'restarting',
            'message': f'Воркеры перезапускаются с новыми настройками',
            'output': output,
            'settings': {
                'warmup': {'concurrency': warmup_conc, 'enabled': warmup_enabled},
                'yandex_maps': {'concurrency': maps_conc, 'enabled': maps_enabled},
                'yandex_search': {'concurrency': search_conc, 'enabled': search_enabled},
            }
        }
    except Exception as e:
        logger.error(f"Error restarting workers: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/workers/queues")
async def get_queue_details():
    """Get detailed queue information."""
    try:
        import redis as _redis
        from app.config import settings as _s
        r = _redis.Redis(host=_s.redis_host, port=_s.redis_port)
        
        queues = {}
        for q_name in ['warmup', 'yandex_maps', 'yandex_search', 'yandex', 'default', 'proxy', 'maintenance']:
            length = r.llen(q_name) or 0
            queues[q_name] = {
                'length': length,
                'active': length > 0
            }
        
        return queues
    except Exception as e:
        logger.error(f"Error getting queue details: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Advanced API Routes

@router.get("/api/profiles/stats")
async def get_profile_stats(db: Session = Depends(get_db)):
    """Get detailed profile statistics (optimised — pure SQL, no full table load)."""
    from sqlalchemy import func, case
    try:
        # Single aggregation query instead of loading all rows into Python
        row = db.query(
            func.count(BrowserProfile.id).label('total'),
            func.sum(case((BrowserProfile.is_active == True, 1), else_=0)).label('active'),
            func.sum(case((BrowserProfile.warmup_completed == True, 1), else_=0)).label('warmed'),
            func.sum(case(
                (BrowserProfile.warmup_completed == True, case((BrowserProfile.is_active == True, 1), else_=0)),
                else_=0
            )).label('ready_for_tasks'),
        ).first()

        stats = {
            "total": row.total or 0,
            "active": int(row.active or 0),
            "warmed": int(row.warmed or 0),
            "ready_for_tasks": int(row.ready_for_tasks or 0),
            "by_status": {}
        }

        # Status counts via GROUP BY
        status_rows = db.query(
            BrowserProfile.status, func.count(BrowserProfile.id)
        ).group_by(BrowserProfile.status).all()
        stats["by_status"] = {status: cnt for status, cnt in status_rows}

        # Average success rate via SQL
        avg_row = db.query(
            func.avg(
                BrowserProfile.successful_sessions * 100.0 / BrowserProfile.total_sessions
            )
        ).filter(BrowserProfile.total_sessions > 0).first()
        stats["average_success_rate"] = round(float(avg_row[0] or 0), 2)

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


@router.post("/api/proxies/test-all")
async def test_all_proxies(db: Session = Depends(get_db)):
    """Test all proxy servers."""
    try:
        import asyncio
        from core.proxy_manager import ProxyManager

        proxies = db.query(ProxyServer).filter(ProxyServer.is_active == True).all()
        if not proxies:
            return {"message": "No active proxies to test", "results": {"total": 0, "working": 0, "failed": 0}}

        proxy_manager = ProxyManager()
        results = {"total": 0, "working": 0, "failed": 0, "details": []}

        for proxy in proxies:
            proxy_data = {
                'id': proxy.id,
                'host': proxy.host,
                'port': proxy.port,
                'username': proxy.username,
                'password': proxy.password,
                'proxy_type': proxy.proxy_type,
            }

            loop = asyncio.get_event_loop()
            success, response_time, error_message = await loop.run_in_executor(
                None, lambda pd=proxy_data: proxy_manager.test_proxy(pd, timeout=15)
            )

            if success:
                proxy.update_success(response_time)
                results["working"] += 1
            else:
                proxy.update_failure(error_message)
                results["failed"] += 1
            results["total"] += 1

            results["details"].append({
                "proxy_id": proxy.id,
                "name": proxy.name,
                "status": "working" if success else "failed",
                "response_time_ms": round(response_time, 2),
                "error": error_message if not success else None
            })

        db.commit()
        return {"message": f"Tested {results['total']} proxies: {results['working']} working, {results['failed']} failed", "results": results}

    except Exception as e:
        logger.error(f"Error testing all proxies: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to test proxies: {str(e)}")


@router.post("/api/proxies/{proxy_id}/test")
async def test_proxy(proxy_id: int, db: Session = Depends(get_db)):
    """Test a proxy server connection."""
    try:
        proxy = db.query(ProxyServer).filter(ProxyServer.id == proxy_id).first()
        if not proxy:
            raise HTTPException(status_code=404, detail="Proxy not found")

        # Actually test the proxy connection
        import asyncio
        from core.proxy_manager import ProxyManager

        proxy_manager = ProxyManager()
        proxy_data = {
            'id': proxy.id,
            'host': proxy.host,
            'port': proxy.port,
            'username': proxy.username,
            'password': proxy.password,
            'proxy_type': proxy.proxy_type,
        }

        # Run the sync test in a thread pool to not block the event loop
        loop = asyncio.get_event_loop()
        success, response_time, error_message = await loop.run_in_executor(
            None, lambda: proxy_manager.test_proxy(proxy_data, timeout=15)
        )

        # Update proxy status in database
        if success:
            proxy.update_success(response_time)
        else:
            proxy.update_failure(error_message)
        db.commit()

        return {
            "status": "working" if success else "failed",
            "response_time_ms": round(response_time, 2),
            "error": error_message if not success else None,
            "proxy_id": proxy_id
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error testing proxy {proxy_id}: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to test proxy: {str(e)}")


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


@router.post("/api/yandex-targets/{target_id}/launch-visits")
async def launch_visits(target_id: int, body: Dict[str, Any] = None, db: Session = Depends(get_db)):
    """Launch multiple visits for a target using different profiles.
    
    Body params:
        count (int, optional): Number of visits to launch. Defaults to target's visits_per_day.
    """
    import random as _random

    def _log_error_task(db_session, error_msg: str, target_url: str = "", profile_id: int = None):
        """Create a failed Task record so the error appears in Visit Logs."""
        try:
            err_task = Task(
                name=f"Ошибка запуска визита",
                task_type="yandex_visit",
                status="failed",
                target_url=target_url,
                profile_id=profile_id,
                error_message=error_msg,
                created_at=datetime.utcnow(),
                completed_at=datetime.utcnow(),
            )
            err_task.add_log(f"ОШИБКА: {error_msg}")
            db_session.add(err_task)
            db_session.commit()
        except Exception as log_err:
            logger.error(f"Failed to log error task: {log_err}")

    try:
        target = db.query(YandexMapTarget).filter(YandexMapTarget.id == target_id).first()
        if not target:
            _log_error_task(db, f"Цель с ID {target_id} не найдена")
            raise HTTPException(status_code=404, detail="Target not found")

        target_url = target.url or ""
        count = (body or {}).get("count", None) or target.visits_per_day or 10

        # --- Pre-check: Redis connectivity ---
        try:
            import redis
            from app.config import settings as _settings
            r = redis.Redis(host=_settings.redis_host, port=_settings.redis_port, socket_connect_timeout=2)
            r.ping()
        except Exception as redis_err:
            error_msg = f"Redis не запущен или недоступен: {redis_err}. Запустите: redis-server"
            _log_error_task(db, error_msg, target_url)
            raise HTTPException(status_code=503, detail=error_msg)

        # --- Pre-check: Celery tasks import ---
        try:
            from tasks.yandex_maps import visit_yandex_maps_profile_task
        except ImportError as imp_err:
            error_msg = f"Celery задачи недоступны: {imp_err}"
            _log_error_task(db, error_msg, target_url)
            raise HTTPException(status_code=503, detail=error_msg)

        # --- Pre-check: Celery worker availability ---
        try:
            from tasks.celery_app import celery_app as _celery
            inspector = _celery.control.inspect(timeout=2)
            active_workers = inspector.ping()
            if not active_workers:
                error_msg = "Celery worker не запущен. Запустите: celery -A tasks.celery_app:celery_app worker"
                _log_error_task(db, error_msg, target_url)
                raise HTTPException(status_code=503, detail=error_msg)
        except HTTPException:
            raise
        except Exception as celery_err:
            error_msg = f"Не удалось проверить Celery worker: {celery_err}"
            _log_error_task(db, error_msg, target_url)
            raise HTTPException(status_code=503, detail=error_msg)

        # Get warmed profiles
        profiles = db.query(BrowserProfile).filter(
            BrowserProfile.is_active == True,
            BrowserProfile.warmup_completed == True,
            BrowserProfile.status == "warmed"
        ).all()

        if not profiles:
            error_msg = "Нет прогретых профилей. Сначала завершите нагул."
            _log_error_task(db, error_msg, target_url)
            raise HTTPException(status_code=400, detail=error_msg)

        # Filter out profiles that already visited this target
        visited_profile_ids = db.query(ProfileTargetVisit.profile_id).filter(
            ProfileTargetVisit.target_id == target_id,
            ProfileTargetVisit.status == "completed"
        ).all()
        visited_ids = {row[0] for row in visited_profile_ids}

        available_profiles = [p for p in profiles if p.id not in visited_ids]

        if not available_profiles:
            error_msg = f"Все профили уже посещали эту карту ({len(visited_ids)} из {len(profiles)}). Сбросьте визиты."
            _log_error_task(db, error_msg, target_url)
            raise HTTPException(status_code=400, detail=error_msg)

        # Limit count to available profiles
        actual_count = min(count, len(available_profiles))
        selected = _random.sample(available_profiles, actual_count)

        task_params = {
            'min_visit_time': target.min_visit_duration,
            'max_visit_time': target.max_visit_duration,
            'actions': target.enabled_actions.split(',') if target.enabled_actions else []
        }

        launched = []
        for idx, profile in enumerate(selected):
            task = Task.create_yandex_visit_task(
                profile_id=profile.id,
                target_url=target.url,
                parameters=task_params
            )
            db.add(task)
            db.flush()

            # Stagger launches: 5-15 seconds between each visit
            delay_seconds = idx * _random.randint(5, 15)

            try:
                visit_yandex_maps_profile_task.apply_async(
                    args=[profile.id, target.url, task_params, task.id],
                    countdown=delay_seconds,
                    queue='yandex_maps'
                )
            except Exception as delay_err:
                task.status = "failed"
                task.error_message = f"Не удалось отправить задачу в Celery: {delay_err}"
                task.add_log(f"ОШИБКА: {task.error_message}")
                task.completed_at = datetime.utcnow()
                db.flush()
                logger.error(f"Failed to dispatch task for profile {profile.id}: {delay_err}")
                continue

            launched.append({
                "task_id": task.id,
                "profile_id": profile.id,
                "profile_name": profile.name,
                "delay": delay_seconds,
            })

        target.last_visit_at = datetime.utcnow()
        db.commit()

        if not launched:
            raise HTTPException(
                status_code=500,
                detail="Не удалось запустить ни одного визита. Проверьте Redis и Celery worker."
            )

        return {
            "message": f"Запущено {len(launched)} из {count} визитов",
            "launched": len(launched),
            "requested": count,
            "available_profiles": len(available_profiles),
            "tasks": launched,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error launching visits for target {target_id}: {e}")
        db.rollback()
        _log_error_task(db, f"Неожиданная ошибка: {e}", target_url=getattr(target, 'url', '') if 'target' in dir() else '')
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/yandex-targets/{target_id}/visits-per-day")
async def update_visits_per_day(target_id: int, body: Dict[str, Any], db: Session = Depends(get_db)):
    """Update visits_per_day for a target."""
    try:
        target = db.query(YandexMapTarget).filter(YandexMapTarget.id == target_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Target not found")

        new_value = body.get("visits_per_day")
        if new_value is None or int(new_value) < 1:
            raise HTTPException(status_code=400, detail="visits_per_day must be >= 1")

        target.visits_per_day = int(new_value)
        db.commit()

        return {"message": f"Настройка обновлена: {target.visits_per_day} визитов/день", "visits_per_day": target.visits_per_day}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating visits_per_day for target {target_id}: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/visit-logs")
async def get_visit_logs(limit: int = 30, db: Session = Depends(get_db)):
    """Get recent visit task logs for real-time progress display."""
    try:
        from sqlalchemy import case, func
        
        # Priority: in_progress first, then recently completed/failed, pending last
        status_priority = case(
            (Task.status == 'in_progress', 0),
            (Task.status == 'completed', 1),
            (Task.status == 'failed', 1),
            (Task.status == 'pending', 2),
            else_=3
        )
        
        # Sort by status priority, then by most recent activity
        tasks = db.query(Task).filter(
            Task.task_type == "yandex_visit"
        ).order_by(
            status_priority,
            func.coalesce(Task.started_at, Task.created_at).desc()
        ).limit(limit).all()
        
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


@router.post("/api/system/cleanup-chrome")
async def cleanup_chrome_processes():
    """Kill all orphaned Chrome/chromedriver processes."""
    try:
        from core.browser_manager import cleanup_orphaned_chrome
        killed = cleanup_orphaned_chrome()
        return {"message": f"Убито {killed} процессов Chrome/chromedriver", "killed": killed}
    except Exception as e:
        logger.error(f"Error cleaning up Chrome processes: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Process Monitor API — detects stuck/hung processes
# ============================================================

@router.get("/api/process-monitor")
async def get_process_monitor(db: Session = Depends(get_db)):
    """
    Comprehensive process health monitor.
    Detects stuck warmup profiles, orphaned Chrome processes,
    Celery worker/beat issues, and stalled tasks.
    """
    alerts = []  # list of {level: 'danger'|'warning'|'info', title: str, message: str, action: str|None}
    now = datetime.utcnow()

    # ── 1. Stuck warmup profiles (status=warming_up for too long) ──
    stuck_threshold_minutes = 15  # warmup task has 5-min time_limit, so 15 min = definitely stuck
    try:
        from sqlalchemy import func, case
        stuck_cutoff = now - timedelta(minutes=stuck_threshold_minutes)
        # Filter directly in SQL — never loads thousands of non-stuck rows
        warming_profiles = db.query(BrowserProfile).filter(
            BrowserProfile.status == "warming_up",
            func.coalesce(BrowserProfile.updated_at, BrowserProfile.created_at) < stuck_cutoff
        ).limit(50).all()  # cap to avoid loading too many even if all stuck

        stuck_profiles = []
        for p in warming_profiles:
            last_change = p.updated_at or p.created_at
            stuck_profiles.append({
                "id": p.id,
                "name": p.name,
                "stuck_minutes": int((now - last_change).total_seconds() / 60),
                "updated_at": last_change.isoformat()
            })

        if stuck_profiles:
            names = ", ".join(p["name"] for p in stuck_profiles[:5])
            extra = f" и ещё {len(stuck_profiles) - 5}" if len(stuck_profiles) > 5 else ""
            alerts.append({
                "level": "danger",
                "icon": "exclamation-triangle-fill",
                "title": f"🔴 Зависшие профили: {len(stuck_profiles)} шт.",
                "message": f"Профили в статусе 'warming_up' более {stuck_threshold_minutes} мин: {names}{extra}. Процесс прогрева вероятно завис.",
                "action": "fix_stuck_profiles",
                "data": stuck_profiles
            })
    except Exception as e:
        logger.error(f"Process monitor - stuck profiles check error: {e}")

    # ── 2. Failed/error profiles ──
    try:
        error_profiles = db.query(BrowserProfile).filter(
            BrowserProfile.status == "error"
        ).all()
        if error_profiles:
            alerts.append({
                "level": "warning",
                "icon": "exclamation-circle",
                "title": f"⚠️ Профили с ошибками: {len(error_profiles)} шт.",
                "message": f"Профили в статусе 'error' — можно перезапустить прогрев.",
                "action": "restart_error_profiles",
                "data": [{"id": p.id, "name": p.name} for p in error_profiles[:10]]
            })
    except Exception as e:
        logger.error(f"Process monitor - error profiles check: {e}")

    # ── 3. Orphaned Chrome processes ──
    chrome_count = 0
    chromedriver_count = 0
    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'create_time']):
            try:
                name = (proc.info.get('name') or '').lower()
                cmdline = ' '.join(proc.info.get('cmdline') or [])
                if ('chrome' in name and 'chromedriver' not in name
                        and 'browser_profiles' in cmdline):
                    chrome_count += 1
                elif 'chromedriver' in name:
                    chromedriver_count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        # Chrome processes without active Celery tasks = likely orphaned
        active_celery_tasks = 0
        try:
            from tasks.celery_app import celery_app
            inspector = celery_app.control.inspect(timeout=2)
            active = inspector.active()
            if active:
                for w, tasks in active.items():
                    active_celery_tasks += len(tasks)
        except:
            pass

        # Heuristic: each warmup task uses 1 Chrome + 1 chromedriver
        expected_chrome = active_celery_tasks
        orphaned_estimate = max(0, chrome_count - expected_chrome)

        if orphaned_estimate > 2:
            alerts.append({
                "level": "warning",
                "icon": "window-x",
                "title": f"⚠️ Лишние Chrome-процессы: ~{orphaned_estimate}",
                "message": f"Chrome: {chrome_count}, ChromeDriver: {chromedriver_count}, активных задач: {active_celery_tasks}. Возможно есть зависшие браузеры.",
                "action": "cleanup_chrome",
                "data": {"chrome": chrome_count, "chromedriver": chromedriver_count, "active_tasks": active_celery_tasks}
            })
    except ImportError:
        # psutil not installed — try basic check
        try:
            result = subprocess.run(['pgrep', '-f', 'chrome.*browser_profiles'], capture_output=True, text=True, timeout=3)
            if result.stdout.strip():
                chrome_count = len(result.stdout.strip().split('\n'))
        except:
            pass

    # ── 4. Celery Worker health ──
    celery_worker_online = False
    celery_active_tasks = 0
    try:
        from tasks.celery_app import celery_app
        inspector = celery_app.control.inspect(timeout=3)
        ping = inspector.ping()
        if ping:
            celery_worker_online = True
            active = inspector.active()
            if active:
                for w, tasks in active.items():
                    celery_active_tasks += len(tasks)
        else:
            alerts.append({
                "level": "danger",
                "icon": "cpu",
                "title": "🔴 Celery Worker не отвечает!",
                "message": "Worker не запущен или не отвечает. Прогрев и задачи не будут выполняться.",
                "action": None,
                "data": None
            })
    except Exception as e:
        alerts.append({
            "level": "danger",
            "icon": "cpu",
            "title": "🔴 Celery Worker недоступен",
            "message": f"Не удалось проверить статус: {str(e)[:100]}",
            "action": None,
            "data": None
        })

    # ── 5. Celery Beat health ──
    celery_beat_running = False
    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = ' '.join(proc.info.get('cmdline') or [])
                if 'celery' in cmdline and 'beat' in cmdline:
                    celery_beat_running = True
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except ImportError:
        try:
            result = subprocess.run(['pgrep', '-f', 'celery.*beat'], capture_output=True, text=True, timeout=3)
            celery_beat_running = bool(result.stdout.strip())
        except:
            pass

    if not celery_beat_running:
        alerts.append({
            "level": "warning",
            "icon": "clock-history",
            "title": "⚠️ Celery Beat не запущен",
            "message": "Планировщик периодических задач не работает. Автоматические визиты Яндекс Карт и обслуживание не будут запускаться по расписанию.",
            "action": None,
            "data": None
        })

    # ── 6. Stalled tasks (in_progress for too long) ──
    try:
        stalled_threshold = timedelta(minutes=40)  # tasks have 35-min time limit
        stalled_tasks = db.query(Task).filter(
            Task.status == "in_progress",
            Task.started_at.isnot(None),
            Task.started_at < (now - stalled_threshold)
        ).all()

        if stalled_tasks:
            alerts.append({
                "level": "warning",
                "icon": "hourglass-split",
                "title": f"⚠️ Зависшие задачи: {len(stalled_tasks)} шт.",
                "message": f"Задачи в статусе 'in_progress' более 40 мин. Возможно процесс завис.",
                "action": "cancel_stalled_tasks",
                "data": [{"id": t.id, "name": t.name, "type": t.task_type,
                          "started": t.started_at.isoformat() if t.started_at else None} for t in stalled_tasks[:10]]
            })
    except Exception as e:
        logger.error(f"Process monitor - stalled tasks check: {e}")

    # ── 7. Warmup progress stalled (no progress for long time) ──
    try:
        warming_count = db.query(BrowserProfile).filter(BrowserProfile.status == "warming_up").count()
        warmed_count = db.query(BrowserProfile).filter(BrowserProfile.warmup_completed == True).count()
        total_count = db.query(BrowserProfile).count()

        if warming_count == 0 and warmed_count < total_count and total_count > 0 and celery_worker_online:
            pending_count = total_count - warmed_count - warming_count
            if pending_count > 0:
                alerts.append({
                    "level": "info",
                    "icon": "info-circle",
                    "title": f"ℹ️ Прогрев на паузе: {pending_count} профилей ожидают",
                    "message": f"Прогрето: {warmed_count}/{total_count}. Нет активных задач прогрева. Нажмите 'Warm All' для продолжения.",
                    "action": None,
                    "data": None
                })
    except Exception as e:
        logger.error(f"Process monitor - warmup progress check: {e}")

    # ── Summary ──
    summary = {
        "status": "healthy" if not any(a["level"] == "danger" for a in alerts) else "critical",
        "alerts_count": len(alerts),
        "danger_count": sum(1 for a in alerts if a["level"] == "danger"),
        "warning_count": sum(1 for a in alerts if a["level"] == "warning"),
        "info_count": sum(1 for a in alerts if a["level"] == "info"),
        "celery_worker": celery_worker_online,
        "celery_beat": celery_beat_running,
        "celery_active_tasks": celery_active_tasks,
        "chrome_processes": chrome_count,
        "chromedriver_processes": chromedriver_count,
        "checked_at": now.isoformat()
    }

    return {
        "summary": summary,
        "alerts": alerts
    }


@router.post("/api/process-monitor/fix-stuck-profiles")
async def fix_stuck_profiles(db: Session = Depends(get_db)):
    """Reset stuck warming_up profiles back to 'created' so they can be re-warmed."""
    try:
        stuck_threshold = timedelta(minutes=15)
        now = datetime.utcnow()

        stuck = db.query(BrowserProfile).filter(
            BrowserProfile.status == "warming_up",
            BrowserProfile.updated_at < (now - stuck_threshold)
        ).all()

        fixed_count = 0
        for p in stuck:
            p.status = "created" if not p.warmup_completed else "warmed"
            p.updated_at = now
            fixed_count += 1

        db.commit()

        return {
            "message": f"Исправлено {fixed_count} зависших профилей. Они готовы к повторному прогреву.",
            "fixed_count": fixed_count,
            "fixed_profiles": [{"id": p.id, "name": p.name} for p in stuck]
        }
    except Exception as e:
        logger.error(f"Error fixing stuck profiles: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/process-monitor/restart-error-profiles")
async def restart_error_profiles(db: Session = Depends(get_db)):
    """Reset error profiles back to 'created' for re-warmup."""
    try:
        error_profiles = db.query(BrowserProfile).filter(
            BrowserProfile.status == "error"
        ).all()

        fixed_count = 0
        for p in error_profiles:
            p.status = "created"
            p.updated_at = datetime.utcnow()
            fixed_count += 1

        db.commit()

        return {
            "message": f"Сброшено {fixed_count} профилей с ошибками. Готовы к повторному прогреву.",
            "fixed_count": fixed_count
        }
    except Exception as e:
        logger.error(f"Error restarting error profiles: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/process-monitor/cancel-stalled-tasks")
async def cancel_stalled_tasks(db: Session = Depends(get_db)):
    """Cancel tasks that have been in_progress too long."""
    try:
        stalled_threshold = timedelta(minutes=40)
        now = datetime.utcnow()

        stalled = db.query(Task).filter(
            Task.status == "in_progress",
            Task.started_at.isnot(None),
            Task.started_at < (now - stalled_threshold)
        ).all()

        cancelled_count = 0
        for t in stalled:
            t.status = "failed"
            t.error_message = "Автоматически отменена: задача зависла (>40 мин)"
            t.completed_at = now
            cancelled_count += 1

            # Try to revoke Celery task
            if t.celery_task_id:
                try:
                    from tasks.celery_app import celery_app
                    celery_app.control.revoke(t.celery_task_id, terminate=True)
                except:
                    pass

        db.commit()

        return {
            "message": f"Отменено {cancelled_count} зависших задач.",
            "cancelled_count": cancelled_count
        }
    except Exception as e:
        logger.error(f"Error cancelling stalled tasks: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Yandex Search Click-Through API
# ============================================================

@router.get("/api/yandex-search-targets")
async def get_yandex_search_targets(db: Session = Depends(get_db)):
    """Get all Yandex Search click-through targets."""
    try:
        targets = db.query(YandexSearchTarget).order_by(YandexSearchTarget.created_at.desc()).all()
        return [t.to_dict() for t in targets]
    except Exception as e:
        logger.error(f"Error getting search targets: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/yandex-search-targets")
async def create_yandex_search_target(body: Dict[str, Any], db: Session = Depends(get_db)):
    """Create a new Yandex Search target."""
    try:
        domain = body.get("domain", "").strip()
        keywords = body.get("keywords", "").strip()
        if not domain:
            raise HTTPException(status_code=400, detail="Домен обязателен")
        if not keywords:
            raise HTTPException(status_code=400, detail="Ключевые слова обязательны")

        # Clean domain
        domain = domain.replace("https://", "").replace("http://", "").replace("www.", "").rstrip("/")

        target = YandexSearchTarget(
            domain=domain,
            keywords=keywords,
            title=body.get("title", ""),
            visits_per_day=body.get("visits_per_day", 10),
            min_interval_minutes=body.get("min_interval_minutes", 30),
            max_interval_minutes=body.get("max_interval_minutes", 120),
            max_search_pages=body.get("max_search_pages", 3),
            min_time_on_site=body.get("min_time_on_site", 30),
            max_time_on_site=body.get("max_time_on_site", 120),
            concurrent_visits=body.get("concurrent_visits", 1),
            priority=body.get("priority", 5),
            notes=body.get("notes", ""),
        )
        db.add(target)
        db.commit()
        db.refresh(target)
        return target.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating search target: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/yandex-search-targets/{target_id}")
async def update_yandex_search_target(target_id: int, body: Dict[str, Any], db: Session = Depends(get_db)):
    """Update a Yandex Search target."""
    try:
        target = db.query(YandexSearchTarget).filter(YandexSearchTarget.id == target_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Target not found")

        updatable = [
            "domain", "keywords", "title", "visits_per_day",
            "min_interval_minutes", "max_interval_minutes",
            "max_search_pages", "min_time_on_site", "max_time_on_site",
            "concurrent_visits", "priority", "notes"
        ]
        for field in updatable:
            if field in body:
                val = body[field]
                if field == "domain":
                    val = val.replace("https://", "").replace("http://", "").replace("www.", "").rstrip("/")
                setattr(target, field, val)

        db.commit()
        db.refresh(target)
        return target.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating search target: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/yandex-search-targets/{target_id}")
async def delete_yandex_search_target(target_id: int, db: Session = Depends(get_db)):
    """Delete a Yandex Search target."""
    try:
        target = db.query(YandexSearchTarget).filter(YandexSearchTarget.id == target_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Target not found")
        db.delete(target)
        db.commit()
        return {"message": "Deleted", "id": target_id}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/yandex-search-targets/{target_id}/toggle")
async def toggle_yandex_search_target(target_id: int, db: Session = Depends(get_db)):
    """Toggle active status of a search target."""
    try:
        target = db.query(YandexSearchTarget).filter(YandexSearchTarget.id == target_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Target not found")
        target.is_active = not target.is_active
        db.commit()
        return {"id": target_id, "is_active": target.is_active}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/yandex-search-targets/{target_id}/launch")
async def launch_search_visits(target_id: int, body: Dict[str, Any] = None, db: Session = Depends(get_db)):
    """Launch search click-through visits for a target."""
    import random as _random

    try:
        target = db.query(YandexSearchTarget).filter(YandexSearchTarget.id == target_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Target not found")

        body = body or {}
        count = body.get("count", None) or target.visits_per_day or 5

        # Pre-checks
        try:
            import redis
            from app.config import settings as _settings
            r = redis.Redis(host=_settings.redis_host, port=_settings.redis_port, socket_connect_timeout=2)
            r.ping()
        except Exception as redis_err:
            raise HTTPException(status_code=503, detail=f"Redis недоступен: {redis_err}")

        try:
            from tasks.yandex_search import yandex_search_click_task
        except ImportError as imp_err:
            raise HTTPException(status_code=503, detail=f"Celery задачи недоступны: {imp_err}")

        try:
            from tasks.celery_app import celery_app as _celery
            inspector = _celery.control.inspect(timeout=2)
            active_workers = inspector.ping()
            if not active_workers:
                raise HTTPException(status_code=503, detail="Celery worker не запущен")
        except HTTPException:
            raise
        except Exception as celery_err:
            raise HTTPException(status_code=503, detail=f"Celery check failed: {celery_err}")

        # Get warmed profiles
        profiles = db.query(BrowserProfile).filter(
            BrowserProfile.is_active == True,
            BrowserProfile.warmup_completed == True,
            BrowserProfile.status == "warmed"
        ).all()

        if not profiles:
            raise HTTPException(status_code=400, detail="Нет прогретых профилей")

        actual_count = min(count, len(profiles))
        selected = _random.sample(profiles, actual_count)
        keywords = target.get_keywords_list()

        if not keywords:
            raise HTTPException(status_code=400, detail="Нет ключевых слов")

        search_params = {
            'max_search_pages': target.max_search_pages,
            'min_time_on_site': target.min_time_on_site,
            'max_time_on_site': target.max_time_on_site,
        }

        launched = []
        for idx, profile in enumerate(selected):
            keyword = _random.choice(keywords)
            task = Task(
                name=f"Поиск '{keyword}' → {target.domain}",
                task_type="yandex_search",
                profile_id=profile.id,
                target_url=f"https://yandex.ru/search/?text={keyword}",
                parameters={
                    'keyword': keyword,
                    'domain': target.domain,
                    'target_id': target.id,
                    **search_params
                },
                priority="high",
                status="pending"
            )
            db.add(task)
            db.flush()

            delay_seconds = idx * _random.randint(10, 30)

            try:
                yandex_search_click_task.apply_async(
                    args=[profile.id, target.id, keyword, task.id, search_params],
                    countdown=delay_seconds,
                    queue='yandex_search'
                )
                task.status = "pending"
                launched.append({
                    "task_id": task.id,
                    "profile_id": profile.id,
                    "keyword": keyword,
                    "delay": delay_seconds
                })
            except Exception as send_err:
                task.status = "failed"
                task.error_message = str(send_err)

        db.commit()

        return {
            "message": f"Запущено {len(launched)} поисковых визитов",
            "launched": len(launched),
            "total_profiles": len(profiles),
            "target": target.domain,
            "tasks": launched
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error launching search visits: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/yandex-search-targets/{target_id}/reset-stats")
async def reset_search_target_stats(target_id: int, db: Session = Depends(get_db)):
    """Reset statistics for a search target."""
    try:
        target = db.query(YandexSearchTarget).filter(YandexSearchTarget.id == target_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Target not found")
        target.total_visits = 0
        target.successful_visits = 0
        target.failed_visits = 0
        target.not_found_count = 0
        target.today_visits = 0
        target.today_successful = 0
        target.today_failed = 0
        db.commit()
        return {"message": "Stats reset", "id": target_id}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/yandex-search-logs")
async def get_yandex_search_logs(db: Session = Depends(get_db), limit: int = 50):
    """Get recent search click-through task logs."""
    try:
        tasks = db.query(Task).filter(
            Task.task_type == "yandex_search"
        ).order_by(Task.created_at.desc()).limit(limit).all()

        return [t.to_dict() for t in tasks]
    except Exception as e:
        logger.error(f"Error getting search logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))
