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
from app.models.error_log import ErrorLog

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


@router.get("/search-analytics", response_class=HTMLResponse)
async def search_analytics_page(request: Request):
    """Search position analytics page."""
    if not templates:
        return HTMLResponse("<h1>Templates not found</h1>")

    return templates.TemplateResponse("search_analytics.html", {"request": request})


@router.get("/referrer-analytics", response_class=HTMLResponse)
async def referrer_analytics_page(request: Request):
    """Referrer analytics page."""
    if not templates:
        return HTMLResponse("<h1>Templates not found</h1>")

    return templates.TemplateResponse("referrer_analytics.html", {"request": request})


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Worker and system settings page."""
    if not templates:
        return HTMLResponse("<h1>Templates not found</h1>")

    return templates.TemplateResponse("settings.html", {"request": request})


@router.get("/error-logs", response_class=HTMLResponse)
async def error_logs_page(request: Request):
    """Error logs analysis page."""
    if not templates:
        return HTMLResponse("<h1>Templates not found</h1>")

    return templates.TemplateResponse("error_logs.html", {"request": request})


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


@router.get("/api/search-referrer-settings")
async def get_search_referrer_settings(db: Session = Depends(get_db)):
    """Get search referrer settings."""
    try:
        result = {'search_referrer_percent': 50, 'search_referrer_site': 'https://mail.ru'}
        for key in result:
            setting = db.query(UserSettings).filter(UserSettings.setting_key == key).first()
            if setting:
                result[key] = setting.get_typed_value()
        return result
    except Exception as e:
        logger.error(f"Error getting referrer settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/search-referrer-settings")
async def save_search_referrer_settings(data: Dict[str, Any], db: Session = Depends(get_db)):
    """Save search referrer settings."""
    try:
        settings_map = {
            'search_referrer_percent': {'type': 'int', 'desc': 'Процент переходов через реферер (0-100)', 'category': 'yandex_search'},
            'search_referrer_site': {'type': 'string', 'desc': 'URL сайта-реферера (напр. https://mail.ru)', 'category': 'yandex_search'},
        }
        for key, meta in settings_map.items():
            if key in data:
                val = data[key]
                setting = db.query(UserSettings).filter(UserSettings.setting_key == key).first()
                if setting:
                    setting.setting_value = str(val)
                    setting.setting_type = meta['type']
                else:
                    new_s = UserSettings(
                        setting_key=key,
                        setting_value=str(val),
                        setting_type=meta['type'],
                        description=meta['desc'],
                        category=meta['category']
                    )
                    db.add(new_s)
        db.commit()
        return {'status': 'ok'}
    except Exception as e:
        logger.error(f"Error saving referrer settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── AI Persona Settings ─────────────────────────────────────────────────────
@router.get("/api/ai-persona-settings")
async def get_ai_persona_settings(db: Session = Depends(get_db)):
    """Get AI persona generation settings."""
    try:
        defaults = {
            'ai_persona_enabled': True,
            'gemini_model': 'gemini-2.0-flash',
        }
        result = {}
        for key, default_val in defaults.items():
            setting = db.query(UserSettings).filter(UserSettings.setting_key == key).first()
            result[key] = setting.get_typed_value() if setting else default_val

        # Count profiles with/without personas
        total = db.query(BrowserProfile).count()
        with_persona = db.query(BrowserProfile).filter(BrowserProfile.persona_data.isnot(None)).count()
        result['profiles_total'] = total
        result['profiles_with_persona'] = with_persona
        result['profiles_without_persona'] = total - with_persona

        # Count profiles with warmup sites
        # We need to check for warmup_sites key inside persona_data JSON
        try:
            from sqlalchemy import text
            ws_count = db.execute(text(
                "SELECT COUNT(*) FROM browser_profiles "
                "WHERE persona_data IS NOT NULL "
                "AND persona_data::text LIKE '%warmup_sites%' "
                "AND jsonb_array_length(persona_data->'warmup_sites') >= 20"
            )).scalar() or 0
            result['profiles_with_warmup_sites'] = ws_count
            result['profiles_without_warmup_sites'] = with_persona - ws_count
        except Exception:
            result['profiles_with_warmup_sites'] = '?'
            result['profiles_without_warmup_sites'] = '?'

        return result
    except Exception as e:
        logger.error(f"Error getting AI persona settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/ai-persona-settings")
async def save_ai_persona_settings(data: Dict[str, Any], db: Session = Depends(get_db)):
    """Save AI persona generation settings."""
    try:
        settings_map = {
            'ai_persona_enabled': {'type': 'bool', 'desc': 'Включить AI-генерацию персон для новых профилей', 'category': 'ai'},
            'gemini_model': {'type': 'string', 'desc': 'Модель Gemini (gemini-2.0-flash)', 'category': 'ai'},
        }
        for key, meta in settings_map.items():
            if key in data:
                val = data[key]
                setting = db.query(UserSettings).filter(UserSettings.setting_key == key).first()
                if setting:
                    setting.setting_value = str(val)
                    setting.setting_type = meta['type']
                else:
                    new_s = UserSettings(
                        setting_key=key,
                        setting_value=str(val),
                        setting_type=meta['type'],
                        description=meta['desc'],
                        category=meta['category']
                    )
                    db.add(new_s)
        db.commit()
        return {'status': 'ok'}
    except Exception as e:
        logger.error(f"Error saving AI persona settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/ai-persona/generate-for-existing")
async def generate_personas_for_existing(data: Dict[str, Any], db: Session = Depends(get_db)):
    """Generate AI personas for profiles that don't have one yet."""
    try:
        limit = data.get("limit", 50)  # max profiles to process
        overwrite = data.get("overwrite", False)

        query = db.query(BrowserProfile).filter(BrowserProfile.is_active == True)
        if not overwrite:
            query = query.filter(
                (BrowserProfile.persona_data == None) | (BrowserProfile.persona_data == {})
            )
        profiles = query.order_by(BrowserProfile.id).limit(limit).all()

        if not profiles:
            return {"status": "ok", "message": "No profiles need personas", "count": 0}

        from core.ai_persona_generator import generate_personas as _gen_personas, generate_warmup_sites as _gen_warmup

        # Generate personas in batches of 10
        personas_pool = []
        while len(personas_pool) < len(profiles):
            batch = _gen_personas(count=min(10, len(profiles) - len(personas_pool)))
            personas_pool.extend(batch)

        updated = 0
        warmup_sites_generated = 0
        for i, profile in enumerate(profiles):
            if i < len(personas_pool):
                persona = personas_pool[i]
                persona["assigned_profile"] = profile.name
                # Also generate warmup sites for each persona
                try:
                    ws_data = _gen_warmup(persona)
                    persona["warmup_sites"] = ws_data.get("warmup_sites", [])
                    persona["extra_search_queries"] = ws_data.get("extra_search_queries", [])
                    warmup_sites_generated += 1
                except Exception:
                    pass
                profile.persona_data = persona
                # Sync timezone
                if persona.get("timezone"):
                    profile.timezone = persona["timezone"]
                updated += 1

        db.commit()
        logger.info(f"Assigned AI personas to {updated} existing profiles ({warmup_sites_generated} with warmup sites)")

        return {
            "status": "ok",
            "count": updated,
            "warmup_sites_generated": warmup_sites_generated,
            "message": f"Персоны назначены для {updated} профилей (сайты нагула: {warmup_sites_generated})"
        }

    except Exception as e:
        logger.error(f"Error generating personas for existing profiles: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/ai-persona/preview")
async def preview_persona():
    """Generate a preview persona (without saving)."""
    try:
        from core.ai_persona_generator import generate_personas as _gen_personas
        personas = _gen_personas(count=1)
        return personas[0] if personas else {"error": "Failed to generate persona"}
    except Exception as e:
        logger.error(f"Error previewing persona: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/profiles/{profile_id}/persona")
async def get_profile_persona(profile_id: int, db: Session = Depends(get_db)):
    """Get persona data for a specific profile."""
    try:
        profile = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")
        return {
            "profile_id": profile.id,
            "profile_name": profile.name,
            "persona_data": profile.persona_data
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting persona for profile {profile_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/ai-persona/generate-warmup-sites")
async def generate_warmup_sites_for_profiles(data: Dict[str, Any], db: Session = Depends(get_db)):
    """Generate AI warmup sites (50 URLs) for profiles that have personas but no warmup_sites yet."""
    try:
        limit = data.get("limit", 50)
        overwrite = data.get("overwrite", False)

        # Find profiles with persona_data but without warmup_sites
        profiles = db.query(BrowserProfile).filter(
            BrowserProfile.is_active == True,
            BrowserProfile.persona_data != None,
        ).order_by(BrowserProfile.id).limit(limit).all()

        if not profiles:
            return {"status": "ok", "message": "Нет профилей с персонами", "count": 0}

        from core.ai_persona_generator import generate_warmup_sites as _gen_warmup

        updated = 0
        skipped = 0
        errors = 0

        for profile in profiles:
            persona = profile.persona_data
            if not isinstance(persona, dict):
                continue

            # Skip if already has warmup sites (unless overwrite)
            existing_sites = persona.get("warmup_sites", [])
            if isinstance(existing_sites, list) and len(existing_sites) >= 20 and not overwrite:
                skipped += 1
                continue

            try:
                ws_data = _gen_warmup(persona)
                persona["warmup_sites"] = ws_data.get("warmup_sites", [])
                persona["extra_search_queries"] = ws_data.get("extra_search_queries", [])
                profile.persona_data = persona
                # Force SQLAlchemy to detect the change
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(profile, "persona_data")
                updated += 1
            except Exception as e:
                logger.error(f"Error generating warmup sites for profile {profile.id}: {e}")
                errors += 1

        db.commit()
        logger.info(f"Generated warmup sites: {updated} updated, {skipped} skipped, {errors} errors")

        return {
            "status": "ok",
            "count": updated,
            "skipped": skipped,
            "errors": errors,
            "message": f"Сгенерированы сайты нагула для {updated} профилей (пропущено: {skipped})"
        }
    except Exception as e:
        logger.error(f"Error generating warmup sites: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/profiles/{profile_id}/regenerate-warmup-sites")
async def regenerate_warmup_sites_for_profile(profile_id: int, db: Session = Depends(get_db)):
    """Regenerate warmup sites for a specific profile."""
    try:
        profile = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")

        persona = profile.persona_data
        if not isinstance(persona, dict) or not persona.get("name"):
            raise HTTPException(status_code=400, detail="Profile has no AI persona")

        from core.ai_persona_generator import generate_warmup_sites as _gen_warmup

        ws_data = _gen_warmup(persona)
        persona["warmup_sites"] = ws_data.get("warmup_sites", [])
        persona["extra_search_queries"] = ws_data.get("extra_search_queries", [])
        profile.persona_data = persona

        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(profile, "persona_data")
        db.commit()

        return {
            "status": "ok",
            "warmup_sites_count": len(persona.get("warmup_sites", [])),
            "extra_queries_count": len(persona.get("extra_search_queries", [])),
            "message": f"Сгенерировано {len(persona.get('warmup_sites', []))} сайтов нагула"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error regenerating warmup sites for profile {profile_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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

@router.get("/api/profiles/summary")
async def get_profiles_summary(db: Session = Depends(get_db)):
    """Profile status overview for the yandex-targets page."""
    from sqlalchemy import func, case
    try:
        # Count by status
        status_counts = dict(
            db.query(BrowserProfile.status, func.count(BrowserProfile.id))
            .group_by(BrowserProfile.status)
            .all()
        )
        total = sum(status_counts.values())
        warmed = status_counts.get("warmed", 0)
        warming_up = status_counts.get("warming_up", 0)
        created = status_counts.get("created", 0)
        error = status_counts.get("error", 0)

        # Warmed & active (ready for target visits)
        ready = db.query(func.count(BrowserProfile.id)).filter(
            BrowserProfile.is_active == True,
            BrowserProfile.warmup_completed == True,
            BrowserProfile.status == "warmed"
        ).scalar()

        return {
            "total": total,
            "warmed": warmed,
            "warming_up": warming_up,
            "created": created,
            "error": error,
            "ready_for_visits": ready,
        }
    except Exception as e:
        logger.error(f"Error getting profiles summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/yandex-targets/task-status")
async def get_targets_task_status(db: Session = Depends(get_db)):
    """Get real-time task execution status per target."""
    try:
        from sqlalchemy import func, case
        
        # Get all targets
        all_targets = db.query(YandexMapTarget).all()
        
        # Get active tasks (in_progress + pending) grouped by target_url
        active_tasks = db.query(
            Task.target_url,
            func.sum(case(
                (Task.status == 'in_progress', 1),
                else_=0
            )).label('running'),
            func.sum(case(
                (Task.status == 'pending', 1),
                else_=0
            )).label('pending'),
        ).filter(
            Task.task_type == 'yandex_visit',
            Task.status.in_(['in_progress', 'pending']),
        ).group_by(Task.target_url).all()
        
        # Get last completed/failed task per target (within last hour)
        one_hour_ago = datetime.utcnow() - timedelta(hours=1)
        recent_tasks = db.query(
            Task.target_url,
            func.max(case(
                (Task.status == 'completed', Task.completed_at),
                else_=None
            )).label('last_completed'),
            func.max(case(
                (Task.status == 'failed', Task.completed_at),
                else_=None
            )).label('last_failed'),
            func.count(case(
                (Task.status == 'completed', 1),
                else_=None
            )).label('completed_1h'),
            func.count(case(
                (Task.status == 'failed', 1),
                else_=None
            )).label('failed_1h'),
        ).filter(
            Task.task_type == 'yandex_visit',
            Task.created_at >= one_hour_ago,
        ).group_by(Task.target_url).all()
        
        # Build lookup maps
        active_map = {}
        for row in active_tasks:
            active_map[row.target_url] = {
                'running': int(row.running or 0),
                'pending': int(row.pending or 0),
            }
        
        recent_map = {}
        for row in recent_tasks:
            recent_map[row.target_url] = {
                'last_completed': row.last_completed.isoformat() if row.last_completed else None,
                'last_failed': row.last_failed.isoformat() if row.last_failed else None,
                'completed_1h': int(row.completed_1h or 0),
                'failed_1h': int(row.failed_1h or 0),
            }
        
        # Build result per target
        result = {}
        for target in all_targets:
            active = active_map.get(target.url, {'running': 0, 'pending': 0})
            recent = recent_map.get(target.url, {
                'last_completed': None, 'last_failed': None,
                'completed_1h': 0, 'failed_1h': 0
            })
            
            # Determine overall status
            if active['running'] > 0:
                status = 'running'
            elif active['pending'] > 0:
                status = 'pending'
            elif not target.is_active:
                status = 'disabled'
            else:
                status = 'idle'
            
            result[str(target.id)] = {
                'status': status,
                'running': active['running'],
                'pending': active['pending'],
                'last_completed': recent.get('last_completed'),
                'last_failed': recent.get('last_failed'),
                'completed_1h': recent.get('completed_1h', 0),
                'failed_1h': recent.get('failed_1h', 0),
            }
        
        return result
    except Exception as e:
        logger.error(f"Error getting targets task status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
    Results are cached in Redis for 30 seconds to avoid slow Celery inspect calls.
    """
    # ── Cache check — return cached result if fresh ──
    try:
        import redis as _redis_cache
        import json as _json_cache
        from app.config import settings as _s_cache
        _r_cache = _redis_cache.Redis(host=_s_cache.redis_host, port=_s_cache.redis_port, decode_responses=True)
        cached = _r_cache.get('process_monitor_cache')
        if cached:
            return _json_cache.loads(cached)
    except Exception:
        _r_cache = None

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
            inspector = celery_app.control.inspect(timeout=1)
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
        inspector = celery_app.control.inspect(timeout=1)
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
    # Beat runs in a separate container, so psutil/pgrep won't find it.
    # Instead, check if scheduled tasks were dispatched recently via Redis
    # or if beat schedule keys exist.
    celery_beat_running = False
    try:
        import redis as _redis_beat
        from app.config import settings as _s_beat
        _r_beat = _redis_beat.Redis(host=_s_beat.redis_host, port=_s_beat.redis_port, decode_responses=True)
        # Check if celery beat schedule key exists (celery stores it in Redis)
        # Also check if any of the periodic queues received tasks recently
        # by looking at queue activity — if schedule_visits tasks are being
        # sent every 5 min, there should be recent activity
        beat_keys = _r_beat.keys('celery-beat*') or []
        if beat_keys:
            celery_beat_running = True
        else:
            # Fallback: check recent task activity from scheduled tasks
            # If yandex_maps or yandex_search queues have had activity,
            # beat is likely running
            try:
                from tasks.celery_app import celery_app as _celery_beat
                inspect_beat = _celery_beat.control.inspect(timeout=1)
                scheduled = inspect_beat.scheduled() or {}
                reserved = inspect_beat.reserved() or {}
                # If workers have scheduled/reserved tasks, beat is dispatching
                if scheduled or reserved:
                    celery_beat_running = True
                else:
                    # Final fallback: check if any worker received tasks recently
                    active = inspect_beat.active() or {}
                    if active:
                        celery_beat_running = True
            except Exception:
                pass
    except Exception:
        # If Redis check fails, assume beat is running to avoid false alerts
        celery_beat_running = True

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

    result = {
        "summary": summary,
        "alerts": alerts
    }

    # ── Cache the result for 30 seconds ──
    try:
        if _r_cache:
            import json as _json_store
            _r_cache.setex('process_monitor_cache', 30, _json_store.dumps(result, default=str))
    except Exception:
        pass

    return result


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
        result = []
        # Load keyword frequencies for all targets in one query
        try:
            from app.models.keyword_frequency import KeywordFrequency
            all_freqs = db.query(KeywordFrequency).all()
            freq_map = {}  # target_id -> {keyword: {broad, phrase, exact}}
            for f in all_freqs:
                freq_map.setdefault(f.target_id, {})[f.keyword.lower()] = {
                    "broad": f.freq_broad,
                    "phrase": f.freq_phrase,
                    "exact": f.freq_exact,
                }
        except Exception:
            freq_map = {}

        for t in targets:
            d = t.to_dict()
            d["keyword_frequencies"] = freq_map.get(t.id, {})
            result.append(d)
        return result
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


@router.get("/api/yandex-search-targets/{target_id}/not-found-keywords")
async def get_not_found_keywords(target_id: int, db: Session = Depends(get_db), days: int = 1):
    """Get keywords that were checked but never found in search results for the given period."""
    from app.models.search_position_history import SearchPositionHistory
    try:
        target = db.query(YandexSearchTarget).filter(YandexSearchTarget.id == target_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Target not found")

        since = datetime.utcnow() - timedelta(days=days)

        # Only show active keywords (exclude disabled)
        active_keywords = set(k.strip().lower() for k in target.get_active_keywords_list())

        # Get all position history records for this period
        records = db.query(SearchPositionHistory).filter(
            SearchPositionHistory.search_target_id == target_id,
            SearchPositionHistory.checked_at >= since
        ).all()

        # Filter to active keywords only
        if active_keywords:
            records = [r for r in records if r.keyword.strip().lower() in active_keywords]

        # Group by keyword
        keyword_stats = {}
        for r in records:
            kw = r.keyword
            if kw not in keyword_stats:
                keyword_stats[kw] = {"total": 0, "found": 0, "not_found": 0, "last_checked": None}
            keyword_stats[kw]["total"] += 1
            if r.found:
                keyword_stats[kw]["found"] += 1
            else:
                keyword_stats[kw]["not_found"] += 1
            if not keyword_stats[kw]["last_checked"] or (r.checked_at and r.checked_at > keyword_stats[kw]["last_checked"]):
                keyword_stats[kw]["last_checked"] = r.checked_at

        # Keywords that were checked but never found
        not_found_keywords = []
        for kw, stats in keyword_stats.items():
            if stats["found"] == 0 and stats["total"] > 0:
                not_found_keywords.append({
                    "keyword": kw,
                    "checks": stats["total"],
                    "last_checked": stats["last_checked"].isoformat() if stats["last_checked"] else None
                })

        # Also include keywords from target that were never even checked
        all_keywords = target.get_keywords_list()
        checked_keywords = set(keyword_stats.keys())
        never_checked = [kw for kw in all_keywords if kw not in checked_keywords]

        return {
            "target_id": target_id,
            "domain": target.domain,
            "days": days,
            "not_found_keywords": not_found_keywords,
            "never_checked_keywords": never_checked,
            "total_keywords": len(all_keywords),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting not-found keywords: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/yandex-search-targets/{target_id}/remove-keywords")
async def remove_keywords_from_target(target_id: int, body: Dict[str, Any], db: Session = Depends(get_db)):
    """Remove specific keywords from a search target. Body: {keywords: ['kw1', 'kw2']}"""
    try:
        target = db.query(YandexSearchTarget).filter(YandexSearchTarget.id == target_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Target not found")

        keywords_to_remove = set(k.strip().lower() for k in body.get("keywords", []) if k.strip())
        if not keywords_to_remove:
            raise HTTPException(status_code=400, detail="Не указаны ключевые слова для удаления")

        current_keywords = target.get_keywords_list()
        original_count = len(current_keywords)

        # Filter out keywords to remove (case-insensitive match)
        remaining = [kw for kw in current_keywords if kw.strip().lower() not in keywords_to_remove]
        removed_count = original_count - len(remaining)

        if removed_count == 0:
            return {"message": "Ни одно ключевое слово не найдено для удаления", "removed": 0, "remaining": original_count}

        if len(remaining) == 0:
            raise HTTPException(status_code=400, detail="Нельзя удалить все ключевые слова. Должно остаться хотя бы одно.")

        target.keywords = "\n".join(remaining)
        db.commit()

        return {
            "message": f"Удалено {removed_count} ключевых слов",
            "removed": removed_count,
            "remaining": len(remaining),
            "removed_keywords": list(keywords_to_remove & set(kw.strip().lower() for kw in current_keywords)),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing keywords: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/yandex-search-targets/{target_id}/enable-keywords")
async def enable_disabled_keywords(target_id: int, body: Dict[str, Any], db: Session = Depends(get_db)):
    """Re-enable previously auto-disabled keywords. Body: {keywords: ['kw1', 'kw2']} or {all: true}"""
    try:
        target = db.query(YandexSearchTarget).filter(YandexSearchTarget.id == target_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Target not found")

        if body.get("all"):
            count = len(target.get_disabled_keywords_set())
            target.disabled_keywords = None
            db.commit()
            return {"message": f"Все {count} ключевых слов включены", "enabled": count}

        keywords_to_enable = set(k.strip().lower() for k in body.get("keywords", []) if k.strip())
        if not keywords_to_enable:
            raise HTTPException(status_code=400, detail="Не указаны ключевые слова")

        disabled = target.get_disabled_keywords_set()
        remaining = disabled - keywords_to_enable
        enabled_count = len(disabled) - len(remaining)

        if remaining:
            # Reconstruct disabled_keywords from remaining set, preserving original casing
            original_disabled = [k.strip() for k in (target.disabled_keywords or '').split('\n') if k.strip()]
            target.disabled_keywords = '\n'.join(
                k for k in original_disabled if k.strip().lower() in remaining
            )
        else:
            target.disabled_keywords = None

        db.commit()
        return {"message": f"Включено {enabled_count} ключевых слов", "enabled": enabled_count, "still_disabled": len(remaining)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error enabling keywords: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/yandex-search-targets/{target_id}/toggle-keyword")
async def toggle_keyword(target_id: int, body: Dict[str, Any], db: Session = Depends(get_db)):
    """Enable or disable a keyword for click boosting. Body: {keyword: str, enabled: bool}"""
    try:
        target = db.query(YandexSearchTarget).filter(YandexSearchTarget.id == target_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Target not found")

        keyword = (body.get("keyword") or "").strip()
        enabled = body.get("enabled", True)
        if not keyword:
            raise HTTPException(status_code=400, detail="Не указано ключевое слово")

        # Check keyword exists in target
        all_kws_lower = {k.lower() for k in target.get_keywords_list()}
        if keyword.lower() not in all_kws_lower:
            raise HTTPException(status_code=400, detail="Ключевое слово не найдено в цели")

        disabled = target.get_disabled_keywords_set()

        if enabled:
            # Remove from disabled
            if keyword.lower() in disabled:
                original = [k.strip() for k in (target.disabled_keywords or '').split('\n') if k.strip()]
                remaining = [k for k in original if k.strip().lower() != keyword.lower()]
                target.disabled_keywords = '\n'.join(remaining) if remaining else None
        else:
            # Add to disabled
            if keyword.lower() not in disabled:
                existing = target.disabled_keywords.strip() if target.disabled_keywords else ''
                target.disabled_keywords = (existing + '\n' + keyword).strip()

        db.commit()
        db.refresh(target)

        return {
            "keyword": keyword,
            "enabled": enabled,
            "disabled_count": len(target.get_disabled_keywords_set()),
            "active_count": len(target.get_active_keywords_list()),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error toggling keyword: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/yandex-search-targets/{target_id}/toggle-keywords-batch")
async def toggle_keywords_batch(target_id: int, body: Dict[str, Any], db: Session = Depends(get_db)):
    """Enable or disable multiple keywords at once. Body: {keywords: [str], enabled: bool}"""
    try:
        target = db.query(YandexSearchTarget).filter(YandexSearchTarget.id == target_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Target not found")

        keywords = [k.strip() for k in body.get("keywords", []) if k.strip()]
        enabled = body.get("enabled", True)
        if not keywords:
            raise HTTPException(status_code=400, detail="Не указаны ключевые слова")

        all_kws_lower = {k.lower() for k in target.get_keywords_list()}
        disabled = target.get_disabled_keywords_set()

        if enabled:
            # Remove from disabled
            to_enable = {k.lower() for k in keywords if k.lower() in disabled and k.lower() in all_kws_lower}
            if to_enable:
                original = [k.strip() for k in (target.disabled_keywords or '').split('\n') if k.strip()]
                remaining = [k for k in original if k.strip().lower() not in to_enable]
                target.disabled_keywords = '\n'.join(remaining) if remaining else None
            changed = len(to_enable)
        else:
            # Add to disabled
            changed = 0
            for kw in keywords:
                if kw.lower() in all_kws_lower and kw.lower() not in disabled:
                    existing = target.disabled_keywords.strip() if target.disabled_keywords else ''
                    target.disabled_keywords = (existing + '\n' + kw).strip()
                    disabled.add(kw.lower())
                    changed += 1

        db.commit()
        db.refresh(target)

        return {
            "changed": changed,
            "enabled": enabled,
            "disabled_count": len(target.get_disabled_keywords_set()),
            "active_count": len(target.get_active_keywords_list()),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error batch toggling keywords: {e}")
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


@router.get("/api/yandex-search-stats")
async def get_yandex_search_real_stats(db: Session = Depends(get_db)):
    """Get real click statistics from tasks table (not from target counters).
    
    Returns: today clicks (completed/failed/total), yesterday clicks,
    captcha encounters, per-domain breakdown.
    """
    try:
        from sqlalchemy import func as sqla_func, case, cast, Date

        now = datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday_start = today_start - timedelta(days=1)

        # --- Today's stats from tasks table ---
        today_tasks = db.query(
            sqla_func.count().label('total'),
            sqla_func.sum(case((Task.status == 'completed', 1), else_=0)).label('completed'),
            sqla_func.sum(case((Task.status == 'failed', 1), else_=0)).label('failed'),
            sqla_func.sum(case((Task.status == 'not_found', 1), else_=0)).label('not_found'),
            sqla_func.sum(case((Task.status == 'in_progress', 1), else_=0)).label('in_progress'),
            sqla_func.sum(case((Task.status == 'pending', 1), else_=0)).label('pending'),
        ).filter(
            Task.task_type == "yandex_search",
            Task.created_at >= today_start,
        ).first()

        # --- Yesterday's stats ---
        yesterday_tasks = db.query(
            sqla_func.count().label('total'),
            sqla_func.sum(case((Task.status == 'completed', 1), else_=0)).label('completed'),
            sqla_func.sum(case((Task.status == 'failed', 1), else_=0)).label('failed'),
            sqla_func.sum(case((Task.status == 'not_found', 1), else_=0)).label('not_found'),
        ).filter(
            Task.task_type == "yandex_search",
            Task.created_at >= yesterday_start,
            Task.created_at < today_start,
        ).first()

        # --- Captcha stats (from task logs containing captcha mentions) ---
        captcha_today = db.query(sqla_func.count()).filter(
            Task.task_type == "yandex_search",
            Task.created_at >= today_start,
            Task.execution_logs.ilike('%капч%'),
        ).scalar() or 0

        captcha_yesterday = db.query(sqla_func.count()).filter(
            Task.task_type == "yandex_search",
            Task.created_at >= yesterday_start,
            Task.created_at < today_start,
            Task.execution_logs.ilike('%капч%'),
        ).scalar() or 0

        # --- Per-domain breakdown (today) ---
        # We need to join with parameters to get the domain
        all_today = db.query(Task).filter(
            Task.task_type == "yandex_search",
            Task.created_at >= today_start,
        ).all()

        domain_stats = {}
        for t in all_today:
            domain = (t.parameters or {}).get('domain', 'unknown')
            if domain not in domain_stats:
                domain_stats[domain] = {'completed': 0, 'failed': 0, 'not_found': 0, 'total': 0, 'in_progress': 0, 'pending': 0}
            domain_stats[domain]['total'] += 1
            if t.status == 'completed':
                domain_stats[domain]['completed'] += 1
            elif t.status == 'failed':
                domain_stats[domain]['failed'] += 1
            elif t.status == 'not_found':
                domain_stats[domain]['not_found'] += 1
            elif t.status == 'in_progress':
                domain_stats[domain]['in_progress'] += 1
            elif t.status == 'pending':
                domain_stats[domain]['pending'] += 1

        # --- Per-domain breakdown (yesterday) ---
        all_yesterday = db.query(Task).filter(
            Task.task_type == "yandex_search",
            Task.created_at >= yesterday_start,
            Task.created_at < today_start,
        ).all()

        domain_stats_yesterday = {}
        for t in all_yesterday:
            domain = (t.parameters or {}).get('domain', 'unknown')
            if domain not in domain_stats_yesterday:
                domain_stats_yesterday[domain] = {'completed': 0, 'failed': 0, 'not_found': 0, 'total': 0}
            domain_stats_yesterday[domain]['total'] += 1
            if t.status == 'completed':
                domain_stats_yesterday[domain]['completed'] += 1
            elif t.status == 'failed':
                domain_stats_yesterday[domain]['failed'] += 1
            elif t.status == 'not_found':
                domain_stats_yesterday[domain]['not_found'] += 1

        return {
            "today": {
                "total": today_tasks.total or 0,
                "completed": int(today_tasks.completed or 0),
                "failed": int(today_tasks.failed or 0),
                "not_found": int(today_tasks.not_found or 0),
                "in_progress": int(today_tasks.in_progress or 0),
                "pending": int(today_tasks.pending or 0),
            },
            "yesterday": {
                "total": yesterday_tasks.total or 0,
                "completed": int(yesterday_tasks.completed or 0),
                "failed": int(yesterday_tasks.failed or 0),
                "not_found": int(yesterday_tasks.not_found or 0),
            },
            "captcha": {
                "today": captcha_today,
                "yesterday": captcha_yesterday,
            },
            "per_domain_today": domain_stats,
            "per_domain_yesterday": domain_stats_yesterday,
        }
    except Exception as e:
        logger.error(f"Error getting real search stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ===== Search Position Analytics =====

@router.get("/api/yandex-search-targets/{target_id}/positions-pivot")
async def get_positions_pivot(target_id: int, db: Session = Depends(get_db), days: int = 30):
    """Get keyword × date pivot table with positions. Dates sorted latest first."""
    from app.models.search_position_history import SearchPositionHistory
    from app.models.keyword_frequency import KeywordFrequency
    try:
        target = db.query(YandexSearchTarget).filter(YandexSearchTarget.id == target_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Target not found")

        since = datetime.utcnow() - timedelta(days=days)
        
        # Only show active keywords (exclude disabled)
        active_keywords = set(k.strip().lower() for k in target.get_active_keywords_list())
        
        records = db.query(SearchPositionHistory).filter(
            SearchPositionHistory.search_target_id == target_id,
            SearchPositionHistory.checked_at >= since
        ).order_by(SearchPositionHistory.checked_at.asc()).all()

        # Filter to active keywords only
        if active_keywords:
            records = [r for r in records if r.keyword.strip().lower() in active_keywords]

        if not records:
            return {"target_id": target_id, "domain": target.domain, "dates": [],
                    "keywords": target.get_active_keywords_list(), "pivot": {k: {} for k in target.get_active_keywords_list()}}

        # Collect all dates and keywords, build pivot
        all_dates = set()
        keyword_date_positions = {}  # keyword -> date -> [positions]

        for r in records:
            day_key = r.checked_at.strftime("%Y-%m-%d") if r.checked_at else None
            if not day_key:
                continue
            all_dates.add(day_key)
            kw = r.keyword
            if kw not in keyword_date_positions:
                keyword_date_positions[kw] = {}
            if day_key not in keyword_date_positions[kw]:
                keyword_date_positions[kw][day_key] = {"positions": [], "found": 0, "not_found": 0, "clicked": 0}
            if r.found and r.absolute_position:
                keyword_date_positions[kw][day_key]["positions"].append(r.absolute_position)
                keyword_date_positions[kw][day_key]["found"] += 1
            else:
                keyword_date_positions[kw][day_key]["not_found"] += 1
            if r.clicked:
                keyword_date_positions[kw][day_key]["clicked"] += 1

        # Sort dates latest first
        dates_sorted = sorted(all_dates, reverse=True)

        # Build pivot: keyword -> {date: {position (last check), checks, found, clicked}}
        pivot = {}
        for kw, date_data in keyword_date_positions.items():
            pivot[kw] = {}
            for d in dates_sorted:
                if d in date_data:
                    dd = date_data[d]
                    # Use the LAST position of the day (most recent check)
                    last_pos = dd["positions"][-1] if dd["positions"] else None
                    pivot[kw][d] = {
                        "avg_position": last_pos,
                        "checks": dd["found"] + dd["not_found"],
                        "found": dd["found"],
                        "not_found": dd["not_found"],
                        "clicked": dd["clicked"]
                    }
                else:
                    pivot[kw][d] = None

        # Add active keywords that have no history records yet
        for ak in target.get_active_keywords_list():
            if ak not in pivot:
                pivot[ak] = {d: None for d in dates_sorted}

        # Sort keywords by their latest average position (best first)
        def kw_sort_key(kw_name):
            for d in dates_sorted:
                cell = pivot[kw_name].get(d)
                if cell and cell["avg_position"] is not None:
                    return cell["avg_position"]
            return 999

        keywords_sorted = sorted(pivot.keys(), key=kw_sort_key)

        # Load keyword frequencies from DB
        freq_records = db.query(KeywordFrequency).filter(
            KeywordFrequency.target_id == target_id
        ).all()
        keyword_frequencies = {}
        for fr in freq_records:
            keyword_frequencies[fr.keyword] = {
                "broad": fr.freq_broad,
                "phrase": fr.freq_phrase,
                "exact": fr.freq_exact,
            }

        return {
            "target_id": target_id,
            "domain": target.domain,
            "dates": dates_sorted,
            "keywords": keywords_sorted,
            "pivot": pivot,
            "keyword_frequencies": keyword_frequencies,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting positions pivot: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/yandex-search-targets/{target_id}/position-history")
async def get_position_history(target_id: int, db: Session = Depends(get_db),
                               keyword: str = None, days: int = 30, limit: int = 500):
    """Get position history for a search target, optionally filtered by keyword."""
    from app.models.search_position_history import SearchPositionHistory
    try:
        since = datetime.utcnow() - timedelta(days=days)
        query = db.query(SearchPositionHistory).filter(
            SearchPositionHistory.search_target_id == target_id,
            SearchPositionHistory.checked_at >= since
        )
        if keyword:
            query = query.filter(SearchPositionHistory.keyword == keyword)
        
        records = query.order_by(SearchPositionHistory.checked_at.desc()).limit(limit).all()
        return [r.to_dict() for r in records]
    except Exception as e:
        logger.error(f"Error getting position history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/yandex-search-targets/{target_id}/analytics")
async def get_search_analytics(target_id: int, db: Session = Depends(get_db), days: int = 30):
    """Get aggregated analytics for a search target: per-keyword trends, growth/decline."""
    from app.models.search_position_history import SearchPositionHistory
    from sqlalchemy import func, case
    try:
        target = db.query(YandexSearchTarget).filter(YandexSearchTarget.id == target_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Target not found")
        
        since = datetime.utcnow() - timedelta(days=days)
        
        # Only show active keywords (exclude disabled)
        active_keywords = set(k.strip().lower() for k in target.get_active_keywords_list())
        
        # Get all records for the period
        records = db.query(SearchPositionHistory).filter(
            SearchPositionHistory.search_target_id == target_id,
            SearchPositionHistory.checked_at >= since
        ).order_by(SearchPositionHistory.checked_at.asc()).all()
        
        # Filter to active keywords only
        if active_keywords:
            records = [r for r in records if r.keyword.strip().lower() in active_keywords]
        
        active_keywords_list = target.get_active_keywords_list()
        
        if not records:
            # Still return all active keywords with empty data
            empty_keywords = [{
                "keyword": ak,
                "total_checks": 0, "found_count": 0, "not_found_count": 0, "found_rate": 0,
                "avg_position": None, "current_avg": None, "best_position": None, "worst_position": None,
                "trend": "no_data", "trend_value": 0, "chart_data": []
            } for ak in active_keywords_list]
            return {
                "target_id": target_id,
                "domain": target.domain,
                "period_days": days,
                "total_checks": 0,
                "keywords": empty_keywords,
                "summary": {"avg_position": None, "found_rate": 0, "trend": "no_data", "keywords_count": len(active_keywords_list)}
            }
        
        # Group by keyword
        keyword_data = {}
        for r in records:
            kw = r.keyword
            if kw not in keyword_data:
                keyword_data[kw] = []
            keyword_data[kw].append(r)
        
        # Analyze each keyword
        keyword_analytics = []
        total_found = 0
        total_checks = len(records)
        all_positions = []
        
        for kw, kw_records in keyword_data.items():
            found_records = [r for r in kw_records if r.found]
            not_found = len(kw_records) - len(found_records)
            
            total_found += len(found_records)
            
            positions = [r.absolute_position for r in found_records if r.absolute_position]
            all_positions.extend(positions)
            
            # Use the LAST (most recent) position instead of average
            avg_pos = positions[-1] if positions else None
            
            # Trend: compare first half vs second half
            trend = "stable"
            trend_value = 0
            if len(positions) >= 4:
                mid = len(positions) // 2
                first_half_avg = sum(positions[:mid]) / mid
                second_half_avg = sum(positions[mid:]) / (len(positions) - mid)
                trend_value = round(first_half_avg - second_half_avg, 1)  # positive = improving (lower position = better)
                if trend_value > 2:
                    trend = "improving"
                elif trend_value < -2:
                    trend = "declining"
            
            # Best and worst positions
            best_pos = min(positions) if positions else None
            worst_pos = max(positions) if positions else None
            
            # Current position = last successful check
            last_found = [r for r in kw_records if r.found and r.absolute_position]
            current_avg = last_found[-1].absolute_position if last_found else None
            
            # Position history for chart (aggregate by date)
            daily_positions = {}
            for r in kw_records:
                day_key = r.checked_at.strftime("%Y-%m-%d") if r.checked_at else None
                if day_key:
                    if day_key not in daily_positions:
                        daily_positions[day_key] = {"positions": [], "found": 0, "not_found": 0}
                    if r.found and r.absolute_position:
                        daily_positions[day_key]["positions"].append(r.absolute_position)
                        daily_positions[day_key]["found"] += 1
                    else:
                        daily_positions[day_key]["not_found"] += 1
            
            chart_data = []
            for day, data in sorted(daily_positions.items()):
                # Use LAST position of the day
                last_day_pos = data["positions"][-1] if data["positions"] else None
                chart_data.append({
                    "date": day,
                    "avg_position": last_day_pos,
                    "checks": data["found"] + data["not_found"],
                    "found": data["found"],
                    "not_found": data["not_found"]
                })
            
            keyword_analytics.append({
                "keyword": kw,
                "total_checks": len(kw_records),
                "found_count": len(found_records),
                "not_found_count": not_found,
                "found_rate": round(len(found_records) / len(kw_records) * 100, 1) if kw_records else 0,
                "avg_position": avg_pos,
                "current_avg": current_avg,
                "best_position": best_pos,
                "worst_position": worst_pos,
                "trend": trend,
                "trend_value": trend_value,
                "chart_data": chart_data
            })
        
        # Add active keywords that have no history records yet
        seen_keywords = {ka["keyword"].strip().lower() for ka in keyword_analytics}
        for ak in target.get_active_keywords_list():
            if ak.strip().lower() not in seen_keywords:
                keyword_analytics.append({
                    "keyword": ak,
                    "total_checks": 0,
                    "found_count": 0,
                    "not_found_count": 0,
                    "found_rate": 0,
                    "avg_position": None,
                    "current_avg": None,
                    "best_position": None,
                    "worst_position": None,
                    "trend": "no_data",
                    "trend_value": 0,
                    "chart_data": []
                })
        
        # Sort by avg position (best first), keywords not found go last
        keyword_analytics.sort(key=lambda x: (x["avg_position"] is None, x["avg_position"] or 999))
        
        # Overall summary — last position across all keywords
        overall_avg = all_positions[-1] if all_positions else None
        overall_found_rate = round(total_found / total_checks * 100, 1) if total_checks else 0
        
        # Overall trend
        overall_trend = "no_data"
        if len(all_positions) >= 4:
            mid = len(all_positions) // 2
            first_avg = sum(all_positions[:mid]) / mid
            second_avg = sum(all_positions[mid:]) / (len(all_positions) - mid)
            diff = first_avg - second_avg
            if diff > 2:
                overall_trend = "improving"
            elif diff < -2:
                overall_trend = "declining"
            else:
                overall_trend = "stable"
        
        # === TOP distribution by day ===
        # For each day, count how many UNIQUE keywords had their LAST position in TOP-3/5/10/20/50
        # Uses last (most recent) position of the day, same as pivot table
        daily_keyword_last = {}  # {date: {keyword: last_position}}
        for r in records:
            if r.found and r.absolute_position and r.checked_at:
                day_key = r.checked_at.strftime("%Y-%m-%d")
                kw = r.keyword
                if day_key not in daily_keyword_last:
                    daily_keyword_last[day_key] = {}
                # Records are ordered by checked_at ASC, so last write = latest
                daily_keyword_last[day_key][kw] = r.absolute_position
        
        top_distribution = []
        for day in sorted(daily_keyword_last.keys()):
            kw_positions = daily_keyword_last[day]
            total_kw_day = len(kw_positions)
            top3 = sum(1 for p in kw_positions.values() if p <= 3)
            top5 = sum(1 for p in kw_positions.values() if p <= 5)
            top10 = sum(1 for p in kw_positions.values() if p <= 10)
            top20 = sum(1 for p in kw_positions.values() if p <= 20)
            top50 = sum(1 for p in kw_positions.values() if p <= 50)
            top_distribution.append({
                "date": day,
                "top3": top3,
                "top5": top5,
                "top10": top10,
                "top20": top20,
                "top50": top50,
                "total_keywords": total_kw_day
            })
        
        return {
            "target_id": target_id,
            "domain": target.domain,
            "period_days": days,
            "total_checks": total_checks,
            "keywords": keyword_analytics,
            "top_distribution": top_distribution,
            "summary": {
                "avg_position": overall_avg,
                "found_rate": overall_found_rate,
                "trend": overall_trend,
                "total_found": total_found,
                "total_not_found": total_checks - total_found,
                "keywords_count": len(keyword_analytics)
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting search analytics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/referrer-analytics")
async def get_referrer_analytics(db: Session = Depends(get_db), days: int = 30):
    """Compare position data for visits with referrer vs without referrer."""
    from app.models.search_position_history import SearchPositionHistory
    try:
        since = datetime.utcnow() - timedelta(days=days)
        
        records = db.query(SearchPositionHistory).filter(
            SearchPositionHistory.checked_at >= since
        ).order_by(SearchPositionHistory.checked_at.asc()).all()
        
        if not records:
            return {"has_data": False, "message": "Нет данных за выбранный период"}
        
        # Split into referrer / no-referrer groups
        with_ref = [r for r in records if r.referrer_used]
        without_ref = [r for r in records if not r.referrer_used]
        
        def analyze_group(group_records):
            """Compute stats for a group of position records."""
            total = len(group_records)
            found = [r for r in group_records if r.found]
            found_with_pos = [r for r in found if r.absolute_position]
            positions = [r.absolute_position for r in found_with_pos]
            clicked = [r for r in group_records if r.clicked]
            
            avg_pos = round(sum(positions) / len(positions), 1) if positions else None
            
            # Trend: first half vs second half
            trend_value = 0
            trend = "no_data"
            if len(positions) >= 4:
                mid = len(positions) // 2
                first_avg = sum(positions[:mid]) / mid
                second_avg = sum(positions[mid:]) / (len(positions) - mid)
                trend_value = round(first_avg - second_avg, 1)
                if trend_value > 2:
                    trend = "improving"
                elif trend_value < -2:
                    trend = "declining"
                else:
                    trend = "stable"
            
            # TOP distribution
            top3 = sum(1 for p in positions if p <= 3)
            top5 = sum(1 for p in positions if p <= 5)
            top10 = sum(1 for p in positions if p <= 10)
            
            # Daily average positions for chart
            daily = {}
            for r in group_records:
                if not r.checked_at:
                    continue
                day = r.checked_at.strftime("%Y-%m-%d")
                if day not in daily:
                    daily[day] = {"positions": [], "found": 0, "not_found": 0, "clicked": 0}
                if r.found and r.absolute_position:
                    daily[day]["positions"].append(r.absolute_position)
                    daily[day]["found"] += 1
                else:
                    daily[day]["not_found"] += 1
                if r.clicked:
                    daily[day]["clicked"] += 1
            
            chart_data = []
            for day in sorted(daily.keys()):
                d = daily[day]
                avg = round(sum(d["positions"]) / len(d["positions"]), 1) if d["positions"] else None
                chart_data.append({
                    "date": day,
                    "avg_position": avg,
                    "found": d["found"],
                    "not_found": d["not_found"],
                    "clicked": d["clicked"],
                    "total": d["found"] + d["not_found"],
                })
            
            return {
                "total": total,
                "found": len(found),
                "not_found": total - len(found),
                "found_rate": round(len(found) / total * 100, 1) if total else 0,
                "clicked": len(clicked),
                "avg_position": avg_pos,
                "trend": trend,
                "trend_value": trend_value,
                "top3": top3,
                "top5": top5,
                "top10": top10,
                "chart_data": chart_data,
            }
        
        # Per-keyword breakdown
        keyword_comparison = {}
        for r in records:
            kw = r.keyword
            if kw not in keyword_comparison:
                keyword_comparison[kw] = {"with_ref": [], "without_ref": []}
            if r.referrer_used:
                keyword_comparison[kw]["with_ref"].append(r)
            else:
                keyword_comparison[kw]["without_ref"].append(r)
        
        keywords_data = []
        for kw, groups in keyword_comparison.items():
            wr = groups["with_ref"]
            wor = groups["without_ref"]
            wr_positions = [r.absolute_position for r in wr if r.found and r.absolute_position]
            wor_positions = [r.absolute_position for r in wor if r.found and r.absolute_position]
            wr_avg = round(sum(wr_positions) / len(wr_positions), 1) if wr_positions else None
            wor_avg = round(sum(wor_positions) / len(wor_positions), 1) if wor_positions else None
            keywords_data.append({
                "keyword": kw,
                "with_ref_count": len(wr),
                "without_ref_count": len(wor),
                "with_ref_avg": wr_avg,
                "without_ref_avg": wor_avg,
                "diff": round(wor_avg - wr_avg, 1) if wr_avg and wor_avg else None,
            })
        
        keywords_data.sort(key=lambda x: (x["diff"] is None, -(x["diff"] or 0)))
        
        # Count records that have referrer_used data (non-null / not all False from old data)
        has_referrer_data = len(with_ref) > 0
        
        return {
            "has_data": True,
            "has_referrer_data": has_referrer_data,
            "period_days": days,
            "total_records": len(records),
            "with_referrer": analyze_group(with_ref),
            "without_referrer": analyze_group(without_ref),
            "keywords": keywords_data,
        }
    except Exception as e:
        logger.error(f"Error getting referrer analytics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/yandex-search-targets/{target_id}/strategy")
async def get_search_strategy(target_id: int, db: Session = Depends(get_db)):
    """Generate strategy recommendations using the position-adaptive click algorithm."""
    from app.models.search_position_history import SearchPositionHistory
    from app.models.keyword_frequency import KeywordFrequency
    from tasks.yandex_search import _calculate_keyword_clicks
    try:
        target = db.query(YandexSearchTarget).filter(YandexSearchTarget.id == target_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Target not found")
        
        keywords_list = target.get_keywords_list()
        
        # Use success rate for correction (if enough data)
        sr = target.success_rate if target.total_visits >= 10 else 100.0
        
        # Load exact frequency data for priority weighting
        freq_weights = {}
        freq_values = {}
        try:
            freq_records = db.query(KeywordFrequency).filter(
                KeywordFrequency.target_id == target_id
            ).all()
            exact_freqs = {}
            for fr in freq_records:
                freq_values[fr.keyword] = fr.freq_exact or 0
                if fr.freq_exact and fr.freq_exact > 0:
                    exact_freqs[fr.keyword] = fr.freq_exact
            if exact_freqs:
                avg_freq = sum(exact_freqs.values()) / len(exact_freqs)
                if avg_freq > 0:
                    for fkw, fval in exact_freqs.items():
                        freq_weights[fkw] = max(0.7, min(1.5, fval / avg_freq))
        except Exception:
            pass
        
        recommendations = []
        total_budget = 0
        total_done = 0
        
        for kw in keywords_list:
            fw = freq_weights.get(kw, 1.0)
            calc = _calculate_keyword_clicks(db, target.id, kw, target_success_rate=sr, freq_weight=fw)
            remaining = max(0, calc["clicks_per_day"] - calc["today_done"])
            total_budget += calc["clicks_per_day"]
            total_done += calc["today_done"]
            
            # Map phase to priority
            phase = calc["phase"]
            if phase in ("peak", "recovery"):
                priority = "high"
            elif phase in ("ramp_up",):
                priority = "medium"
            elif phase in ("start", "ramp_down", "maintain"):
                priority = "low"
            else:
                priority = "low"
            
            # Map phase to action
            action_map = {
                "start": "start_tracking",
                "ramp_up": "boost",
                "peak": "boost",
                "ramp_down": "maintain",
                "maintain": "maintain",
                "recovery": "boost",
                "not_found": "increase_pages",
                "error": "review"
            }
            
            recommendations.append({
                "keyword": kw,
                "action": action_map.get(phase, "review"),
                "priority": priority,
                "message": calc["reason"],
                "current_position": calc.get("current_position"),
                "prev_position": calc.get("prev_position"),
                "trend": calc["trend"],
                "phase": phase,
                "suggested_clicks": calc["clicks_per_day"],
                "today_done": calc["today_done"],
                "remaining": remaining,
                "freq_exact": freq_values.get(kw),
                "freq_weight": round(fw, 2),
            })
        
        # Sort: high priority first, then by remaining budget desc
        priority_order = {"high": 0, "medium": 1, "low": 2}
        recommendations.sort(key=lambda x: (priority_order.get(x["priority"], 3), -x["remaining"]))
        
        # General advice
        general_advice = []
        top5_count = sum(1 for r in recommendations if r.get("current_position") and r["current_position"] <= 5)
        top10_count = sum(1 for r in recommendations if r.get("current_position") and r["current_position"] <= 10)
        not_found_count = sum(1 for r in recommendations if r.get("phase") == "not_found")
        recovery_count = sum(1 for r in recommendations if r.get("phase") == "recovery")
        peak_count = sum(1 for r in recommendations if r.get("phase") == "peak")
        
        if top5_count > 0:
            general_advice.append(f"✅ {top5_count} ключ(ей) в TOP-5 — режим поддержки")
        if top10_count > top5_count:
            general_advice.append(f"🎯 {top10_count - top5_count} ключ(ей) в TOP-10 — активное продвижение")
        if peak_count > 0:
            general_advice.append(f"🚀 {peak_count} ключ(ей) на пике — максимальные клики")
        if recovery_count > 0:
            general_advice.append(f"⚠️ {recovery_count} ключ(ей) восстанавливаются — позиции падали")
        if not_found_count > 0:
            general_advice.append(f"❌ {not_found_count} ключ(ей) не найдены — проверьте релевантность")
        
        general_advice.append(f"📊 Общий бюджет: {total_budget} кликов/день, выполнено сегодня: {total_done}")
        
        if not general_advice:
            general_advice.append("📊 Данных пока мало. Продолжайте визиты для накопления статистики.")
        
        return {
            "target_id": target_id,
            "domain": target.domain,
            "visits_per_day": target.visits_per_day,
            "algorithm_budget": total_budget,
            "today_done": total_done,
            "recommendations": recommendations,
            "general_advice": general_advice
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating strategy: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Wordstat (keyword frequency)
# ============================================================

@router.post("/api/yandex-search-targets/{target_id}/wordstat")
async def get_wordstat_frequency(target_id: int, body: Dict[str, Any] = {}, db: Session = Depends(get_db)):
    """
    Fetch Yandex Wordstat frequency for all keywords of a target.
    If fresh=true in body, force re-fetch from API; otherwise return cached data from DB.
    """
    from core.wordstat_manager import get_keywords_frequency_batch
    from app.config import settings as _ws_settings
    from app.models.keyword_frequency import KeywordFrequency
    from datetime import datetime

    force_refresh = body.get("fresh", False) if body else False

    try:
        target = db.query(YandexSearchTarget).filter(YandexSearchTarget.id == target_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Target not found")

        keywords = target.get_keywords_list()
        if not keywords:
            return {"target_id": target_id, "frequencies": [], "cached": False}

        # Check if we have cached data
        cached = db.query(KeywordFrequency).filter(
            KeywordFrequency.target_id == target_id
        ).all()
        cached_map = {c.keyword: c for c in cached}

        # If have cached data for all keywords and not forcing refresh → return cache
        if not force_refresh and cached_map and all(kw in cached_map for kw in keywords):
            result = []
            for kw in keywords:
                c = cached_map[kw]
                result.append(c.to_dict())
            return {"target_id": target_id, "domain": target.domain, "frequencies": result, "cached": True}

        # Fetch from API
        api_key = _ws_settings.yandex_search_api_key
        if not api_key:
            raise HTTPException(status_code=400, detail="Yandex Search API key не настроен (YANDEX_BOT_YANDEX_SEARCH_API_KEY)")

        folder_id = _ws_settings.yandex_search_folder_id
        freq_map = get_keywords_frequency_batch(keywords, api_key, folder_id, delay=0.15)

        # Save to DB
        now = datetime.utcnow()
        for kw, (broad, phrase, exact) in freq_map.items():
            existing = cached_map.get(kw)
            if existing:
                existing.freq_broad = broad
                existing.freq_phrase = phrase
                existing.freq_exact = exact
                existing.updated_at = now
            else:
                db.add(KeywordFrequency(
                    target_id=target_id,
                    keyword=kw,
                    freq_broad=broad,
                    freq_phrase=phrase,
                    freq_exact=exact,
                    updated_at=now,
                ))
        # Remove deleted keywords
        for old_kw in cached_map:
            if old_kw not in keywords:
                db.delete(cached_map[old_kw])
        db.commit()

        # Build response
        result = []
        for kw in keywords:
            broad, phrase, exact = freq_map.get(kw, (None, None, None))
            result.append({
                "keyword": kw,
                "freq_broad": broad,
                "freq_phrase": phrase,
                "freq_exact": exact,
                "updated_at": now.isoformat(),
            })

        return {
            "target_id": target_id,
            "domain": target.domain,
            "frequencies": result,
            "cached": False,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Wordstat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Error Logs API
# ============================================================================

@router.get("/api/error-logs")
async def get_error_logs(
    db: Session = Depends(get_db),
    category: str = None,
    domain: str = None,
    days: int = 7,
    limit: int = 200,
):
    """Get error logs with optional filtering."""
    try:
        from sqlalchemy import func as sqla_func

        query = db.query(ErrorLog)

        # Time filter
        cutoff = datetime.utcnow() - timedelta(days=days)
        query = query.filter(ErrorLog.created_at >= cutoff)

        if category:
            query = query.filter(ErrorLog.error_category == category)
        if domain:
            query = query.filter(ErrorLog.domain == domain)

        logs = query.order_by(ErrorLog.created_at.desc()).limit(min(limit, 1000)).all()
        return [log.to_dict() for log in logs]
    except Exception as e:
        logger.error(f"Error getting error logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/error-logs/stats")
async def get_error_logs_stats(
    db: Session = Depends(get_db),
    days: int = 7,
):
    """Get aggregated error statistics."""
    try:
        from sqlalchemy import func as sqla_func

        cutoff = datetime.utcnow() - timedelta(days=days)
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

        # By category (total for period)
        by_category = db.query(
            ErrorLog.error_category,
            sqla_func.count().label('count')
        ).filter(
            ErrorLog.created_at >= cutoff
        ).group_by(ErrorLog.error_category).all()

        # By category (today only)
        by_category_today = db.query(
            ErrorLog.error_category,
            sqla_func.count().label('count')
        ).filter(
            ErrorLog.created_at >= today_start
        ).group_by(ErrorLog.error_category).all()

        # By domain (top 20 for period)
        by_domain = db.query(
            ErrorLog.domain,
            sqla_func.count().label('count')
        ).filter(
            ErrorLog.created_at >= cutoff,
            ErrorLog.domain.isnot(None)
        ).group_by(ErrorLog.domain).order_by(sqla_func.count().desc()).limit(20).all()

        # By proxy (top 10 for period)
        by_proxy = db.query(
            ErrorLog.proxy_host,
            sqla_func.count().label('count')
        ).filter(
            ErrorLog.created_at >= cutoff,
            ErrorLog.proxy_host.isnot(None)
        ).group_by(ErrorLog.proxy_host).order_by(sqla_func.count().desc()).limit(10).all()

        # Daily trend (last N days)
        from sqlalchemy import cast, Date
        daily_trend = db.query(
            cast(ErrorLog.created_at, Date).label('date'),
            sqla_func.count().label('count')
        ).filter(
            ErrorLog.created_at >= cutoff
        ).group_by(cast(ErrorLog.created_at, Date)).order_by(cast(ErrorLog.created_at, Date)).all()

        # Avg duration by category
        avg_duration = db.query(
            ErrorLog.error_category,
            sqla_func.avg(ErrorLog.task_duration_seconds).label('avg_duration')
        ).filter(
            ErrorLog.created_at >= cutoff,
            ErrorLog.task_duration_seconds.isnot(None)
        ).group_by(ErrorLog.error_category).all()

        return {
            "by_category": {r.error_category: r.count for r in by_category},
            "by_category_today": {r.error_category: r.count for r in by_category_today},
            "by_domain": {r.domain: r.count for r in by_domain},
            "by_proxy": {r.proxy_host: r.count for r in by_proxy},
            "daily_trend": [{"date": str(r.date), "count": r.count} for r in daily_trend],
            "avg_duration": {r.error_category: round(r.avg_duration, 1) if r.avg_duration else None for r in avg_duration},
            "total": sum(r.count for r in by_category),
            "total_today": sum(r.count for r in by_category_today),
        }
    except Exception as e:
        logger.error(f"Error getting error stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))
