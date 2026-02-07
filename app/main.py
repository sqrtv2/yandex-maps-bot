"""
Main FastAPI application for Yandex Maps Profile Visitor system.
"""
import os
import logging
from contextlib import asynccontextmanager
from typing import List, Dict, Any
from datetime import datetime

from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import uvicorn

from .config import settings
from .database import get_db, create_tables, db_manager, get_db_session
from .models import BrowserProfile, ProxyServer, Task, UserSettings

# Import web routes
from web.routes import router as web_router

# Import Celery tasks
try:
    from tasks.warmup import warmup_profile_task, warmup_multiple_profiles_task
    CELERY_AVAILABLE = True
    logger = logging.getLogger(__name__)
    logger.info("Celery tasks imported successfully")
except ImportError as e:
    CELERY_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning(f"Celery tasks not available: {e}. Warmup will run in simulation mode.")

# Import domain manager
try:
    from core.domain_manager import domain_manager
    DOMAINS_AVAILABLE = True
    logger = logging.getLogger(__name__)
    logger.info("Domain manager imported successfully")
except ImportError as e:
    DOMAINS_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning(f"Domain manager not available: {e}")

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format=settings.log_format,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(settings.log_file) if settings.log_file else logging.NullHandler()
    ]
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    logger.info("Starting Yandex Maps Profile Visitor system...")

    try:
        # Create database tables
        create_tables()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise

    yield

    # Shutdown
    logger.info("Shutting down Yandex Maps Profile Visitor system...")


# Create FastAPI app
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="System for automated visiting of Yandex Maps profiles",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure this properly in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
static_path = os.path.join(os.path.dirname(__file__), "..", "web", "static")
if os.path.exists(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")

# Setup templates
templates_path = os.path.join(os.path.dirname(__file__), "..", "web", "templates")
if os.path.exists(templates_path):
    templates = Jinja2Templates(directory=templates_path)

# Include web routes
app.include_router(web_router)


# WebSocket manager for real-time updates
class WebSocketManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except:
                # Remove broken connections
                self.active_connections.remove(connection)


manager = WebSocketManager()


# API Routes

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    """Main dashboard page."""
    try:
        # Get dashboard statistics
        profile_count = db.query(BrowserProfile).count()
        proxy_count = db.query(ProxyServer).count()
        task_count = db.query(Task).count()

        return templates.TemplateResponse("index.html", {
            "request": request,
            "profile_count": profile_count,
            "proxy_count": proxy_count,
            "task_count": task_count,
            "app_name": settings.app_name,
            "app_version": settings.app_version
        })
    except Exception as e:
        logger.error(f"Error loading dashboard: {e}")
        return HTMLResponse(
            content=f"<h1>Error Loading Dashboard</h1><p>{str(e)}</p>",
            status_code=500
        )

@app.get("/profiles", response_class=HTMLResponse)
async def profiles_page(request: Request, db: Session = Depends(get_db)):
    """Browser Profiles management page."""
    try:
        profile_count = db.query(BrowserProfile).count()
        return templates.TemplateResponse("profiles.html", {
            "request": request,
            "profile_count": profile_count,
            "app_name": settings.app_name,
            "app_version": settings.app_version
        })
    except Exception as e:
        logger.error(f"Error loading profiles page: {e}")
        return HTMLResponse(
            content=f"<h1>Error Loading Profiles Page</h1><p>{str(e)}</p>",
            status_code=500
        )

@app.get("/proxies", response_class=HTMLResponse)
async def proxies_page(request: Request, db: Session = Depends(get_db)):
    """Proxy Servers management page."""
    try:
        proxy_count = db.query(ProxyServer).count()
        return templates.TemplateResponse("proxies.html", {
            "request": request,
            "proxy_count": proxy_count,
            "app_name": settings.app_name,
            "app_version": settings.app_version
        })
    except Exception as e:
        logger.error(f"Error loading proxies page: {e}")
        return HTMLResponse(
            content=f"<h1>Error Loading Proxies Page</h1><p>{str(e)}</p>",
            status_code=500
        )

@app.get("/tasks", response_class=HTMLResponse)
async def tasks_page(request: Request, db: Session = Depends(get_db)):
    """Tasks management page."""
    try:
        task_count = db.query(Task).count()
        return templates.TemplateResponse("tasks.html", {
            "request": request,
            "task_count": task_count,
            "app_name": settings.app_name,
            "app_version": settings.app_version
        })
    except Exception as e:
        logger.error(f"Error loading tasks page: {e}")
        return HTMLResponse(
            content=f"<h1>Error Loading Tasks Page</h1><p>{str(e)}</p>",
            status_code=500
        )


@app.get("/domains", response_class=HTMLResponse)
async def domains_page(request: Request, db: Session = Depends(get_db)):
    """Domain management page."""
    try:
        # Get basic stats for the page
        profile_count = db.query(BrowserProfile).count()
        return templates.TemplateResponse("domains.html", {
            "request": request,
            "profile_count": profile_count,
            "app_name": settings.app_name,
            "app_version": settings.app_version
        })
    except Exception as e:
        logger.error(f"Error loading domains page: {e}")
        return HTMLResponse(
            content=f"<h1>Error Loading Domains Page</h1><p>{str(e)}</p>",
            status_code=500
        )


@app.get("/api")
async def api_root():
    """API root endpoint."""
    return {
        "message": "Yandex Maps Profile Visitor API",
        "version": settings.app_version,
        "status": "running"
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    try:
        # Check database connection
        from sqlalchemy import text
        with get_db_session() as db:
            db.execute(text("SELECT 1")).scalar()

        return {
            "status": "healthy",
            "database": "connected",
            "version": settings.app_version
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=503, detail="Service unhealthy")


# Browser Profiles API
@app.get("/api/profiles", response_model=Dict[str, Any])
async def get_profiles(
    page: int = 1,
    per_page: int = 50,
    status: str = None,
    warmup_status: str = None,
    search: str = None,
    db: Session = Depends(get_db)
):
    """Get browser profiles with pagination and filtering."""
    try:
        # Validate parameters
        page = max(1, page)
        per_page = min(max(1, per_page), 100)  # Limit to max 100 per page
        offset = (page - 1) * per_page

        # Build query
        query = db.query(BrowserProfile)

        # Apply filters
        if status:
            query = query.filter(BrowserProfile.status == status)
        if warmup_status == "completed":
            query = query.filter(BrowserProfile.warmup_completed == True)
        elif warmup_status == "not_completed":
            query = query.filter(BrowserProfile.warmup_completed == False)
        if search:
            query = query.filter(BrowserProfile.name.ilike(f"%{search}%"))

        # Get total count for pagination
        total_count = query.count()

        # Get paginated results
        profiles = query.order_by(BrowserProfile.id.desc()).offset(offset).limit(per_page).all()

        # Calculate pagination info
        total_pages = (total_count + per_page - 1) // per_page
        has_next = page < total_pages
        has_prev = page > 1

        # Calculate overall progress stats using optimized method
        stats = BrowserProfile.get_warmup_stats(db)

        overall_progress = {
            "total_profiles": stats["total_profiles"],
            "warmed_profiles": stats["warmed_profiles"],
            "warming_profiles": stats["warming_profiles"],
            "progress_percentage": round((stats["warmed_profiles"] / max(stats["total_profiles"], 1)) * 100, 1),
            "warmup_status": "completed" if stats["warming_profiles"] == 0 and stats["warmed_profiles"] == stats["total_profiles"] else "in_progress" if stats["warming_profiles"] > 0 else "not_started"
        }

        return {
            "profiles": [profile.to_dict() for profile in profiles],
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total_count": total_count,
                "total_pages": total_pages,
                "has_next": has_next,
                "has_prev": has_prev
            },
            "overall_progress": overall_progress,
            "filters": {
                "status": status,
                "warmup_status": warmup_status,
                "search": search
            }
        }
    except Exception as e:
        logger.error(f"Error getting profiles: {e}")
        raise HTTPException(status_code=500, detail="Failed to get profiles")


@app.post("/api/profiles")
async def create_profile(profile_data: Dict[str, Any], db: Session = Depends(get_db)):
    """Create a new browser profile."""
    try:
        # Extract auto_start_warmup parameter (default: True for automatic warmup)
        auto_start_warmup = profile_data.get("auto_start_warmup", True)

        profile = BrowserProfile(
            name=profile_data.get("name", "New Profile"),
            user_agent=profile_data.get("user_agent", ""),
            viewport_width=profile_data.get("viewport_width", 1366),
            viewport_height=profile_data.get("viewport_height", 768),
            timezone=profile_data.get("timezone", "Europe/Moscow"),
            language=profile_data.get("language", "ru-RU")
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)

        # Auto-start warmup if requested
        warmup_started = False
        warmup_task_id = None

        if auto_start_warmup:
            try:
                # Set profile status to warming_up
                profile.status = "warming_up"
                db.commit()

                # Start warmup task
                if CELERY_AVAILABLE:
                    task_result = warmup_profile_task.delay(profile.id, duration_minutes=30)
                    warmup_task_id = task_result.id
                    warmup_started = True
                    logger.info(f"Auto-started warmup task {task_result.id} for new profile {profile.id}")
                    await manager.broadcast(f"Profile created and warmup started: {profile.name}")
                else:
                    logger.warning(f"Celery not available, warmup simulation mode for profile {profile.id}")
                    warmup_started = "simulation"
                    await manager.broadcast(f"Profile created (warmup simulation): {profile.name}")

            except Exception as warmup_error:
                logger.error(f"Failed to start warmup for new profile {profile.id}: {warmup_error}")
                # Revert warmup status but keep the profile
                profile.status = "created"
                db.commit()
                await manager.broadcast(f"Profile created (warmup failed): {profile.name}")
        else:
            await manager.broadcast(f"New profile created: {profile.name}")

        # Return profile data with warmup info
        result = profile.to_dict()
        result["warmup_started"] = warmup_started
        result["warmup_task_id"] = warmup_task_id
        result["auto_start_warmup"] = auto_start_warmup

        return result

    except Exception as e:
        logger.error(f"Error creating profile: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create profile")


@app.get("/api/profiles/{profile_id}")
async def get_profile(profile_id: int, db: Session = Depends(get_db)):
    """Get a specific browser profile."""
    try:
        profile = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")
        return profile.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting profile {profile_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get profile")


@app.put("/api/profiles/{profile_id}")
async def update_profile(profile_id: int, profile_data: Dict[str, Any], db: Session = Depends(get_db)):
    """Update a browser profile."""
    try:
        profile = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")

        # Update fields
        for key, value in profile_data.items():
            if hasattr(profile, key):
                setattr(profile, key, value)

        db.commit()
        db.refresh(profile)

        await manager.broadcast(f"Profile updated: {profile.name}")
        return profile.to_dict()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating profile {profile_id}: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update profile")


@app.delete("/api/profiles/{profile_id}")
async def delete_profile(profile_id: int, db: Session = Depends(get_db)):
    """Delete a browser profile."""
    try:
        profile = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")

        profile_name = profile.name
        db.delete(profile)
        db.commit()

        await manager.broadcast(f"Profile deleted: {profile_name}")
        return {"message": "Profile deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting profile {profile_id}: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete profile")


@app.delete("/api/profiles-clear-all")
async def clear_all_profiles(db: Session = Depends(get_db)):
    """Clear all browser profiles from database and file system."""
    try:
        import os
        import shutil
        from pathlib import Path

        # Get all profiles before deletion for counting
        profiles = db.query(BrowserProfile).all()
        profile_count = len(profiles)

        if profile_count == 0:
            return {
                "message": "No profiles to delete",
                "deleted_count": 0,
                "files_deleted": 0
            }

        # Delete from database
        db.query(BrowserProfile).delete()
        db.commit()

        # Delete browser profile directories
        files_deleted = 0
        try:
            browser_profiles_dir = Path(settings.browser_user_data_dir)
            if browser_profiles_dir.exists():
                for profile_dir in browser_profiles_dir.iterdir():
                    if profile_dir.is_dir():
                        shutil.rmtree(profile_dir)
                        files_deleted += 1

        except Exception as file_error:
            logger.warning(f"Error deleting profile directories: {file_error}")

        # Broadcast update
        await manager.broadcast(f"🗑️ All profiles cleared: {profile_count} profiles deleted")

        logger.info(f"Cleared all profiles: {profile_count} profiles, {files_deleted} directories")

        return {
            "message": f"Successfully cleared all profiles",
            "deleted_count": profile_count,
            "files_deleted": files_deleted
        }

    except Exception as e:
        logger.error(f"Error clearing all profiles: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to clear profiles: {str(e)}")


@app.post("/api/profiles-bulk-create")
async def bulk_create_profiles(request_data: Dict[str, Any], db: Session = Depends(get_db)):
    """Create multiple browser profiles at once."""
    try:
        import random
        from fake_useragent import UserAgent

        count = request_data.get("count", 1)
        name_prefix = request_data.get("name_prefix", "Profile-")
        config = request_data.get("config", {})
        auto_start_warmup = request_data.get("auto_start_warmup", False)
        randomize_all = request_data.get("randomize_all", True)

        # Validate count
        if count < 1 or count > 50:
            raise HTTPException(status_code=400, detail="Count must be between 1 and 50")

        # Predefined options for randomization
        viewports = [
            (1366, 768), (1920, 1080), (1440, 900), (1536, 864),
            (1280, 720), (1600, 1200), (2560, 1440), (1024, 768)
        ]

        timezones = [
            "Europe/Moscow", "America/New_York", "Europe/London",
            "Asia/Tokyo", "Europe/Paris", "America/Los_Angeles",
            "Australia/Sydney", "Asia/Shanghai", "Europe/Berlin",
            "America/Chicago", "Europe/Rome", "Asia/Seoul"
        ]

        languages = [
            "ru-RU", "en-US", "en-GB", "ja-JP", "fr-FR", "de-DE",
            "es-ES", "it-IT", "pt-BR", "ko-KR", "zh-CN", "ar-SA"
        ]

        platforms = ["Win32", "MacIntel", "Linux x86_64"]

        # Initialize UserAgent for generating realistic user agents
        ua = UserAgent()

        created_profiles = []

        for i in range(1, count + 1):
            # Generate profile name
            profile_name = f"{name_prefix}{i}"

            # Get configuration values
            if randomize_all or config.get("viewport_width") == "random":
                width, height = random.choice(viewports)
            else:
                width = int(config.get("viewport_width", 1366))
                height = int(config.get("viewport_height", 768))

            if randomize_all or config.get("timezone") == "random":
                timezone = random.choice(timezones)
            else:
                timezone = config.get("timezone", "Europe/Moscow")

            if randomize_all or config.get("language") == "random":
                language = random.choice(languages)
            else:
                language = config.get("language", "ru-RU")

            if randomize_all or config.get("platform") == "random":
                platform = random.choice(platforms)
            else:
                platform = config.get("platform", "Win32")

            # Generate user agent
            user_agent_type = config.get("user_agent_type", "generate")
            if user_agent_type == "generate" or randomize_all:
                try:
                    if platform == "Win32":
                        user_agent = ua.chrome
                    elif platform == "MacIntel":
                        user_agent = ua.safari
                    else:
                        user_agent = ua.firefox
                except:
                    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            elif user_agent_type == "chrome":
                user_agent = ua.chrome if hasattr(ua, 'chrome') else "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            elif user_agent_type == "firefox":
                user_agent = ua.firefox if hasattr(ua, 'firefox') else "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:91.0) Gecko/20100101 Firefox/91.0"
            elif user_agent_type == "safari":
                user_agent = ua.safari if hasattr(ua, 'safari') else "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            else:
                user_agent = ua.random if hasattr(ua, 'random') else "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

            # Create profile
            profile = BrowserProfile(
                name=profile_name,
                user_agent=user_agent,
                viewport_width=width,
                viewport_height=height,
                timezone=timezone,
                language=language,
                platform=platform,
                status="created",
                is_active=True
            )

            db.add(profile)
            created_profiles.append(profile)

        # Commit all profiles
        db.commit()

        # Refresh profiles to get IDs
        for profile in created_profiles:
            db.refresh(profile)

        # Broadcast notification
        await manager.broadcast(f"Bulk created {len(created_profiles)} profiles")

        # Auto-start warmup for all created profiles if requested
        warmup_started = False
        warmup_task_ids = []

        if auto_start_warmup and created_profiles:
            try:
                # Update all created profiles to warming_up status
                profile_ids = []
                for profile in created_profiles:
                    profile.status = "warming_up"
                    profile_ids.append(profile.id)

                db.commit()

                # Start warmup tasks
                if CELERY_AVAILABLE:
                    # Use bulk warmup task for efficiency
                    try:
                        task_result = warmup_multiple_profiles_task.delay(profile_ids, duration_minutes=30)
                        warmup_task_ids.append(task_result.id)
                        warmup_started = True
                        logger.info(f"Auto-started bulk warmup task {task_result.id} for {len(profile_ids)} new profiles")
                        await manager.broadcast(f"Bulk created {len(created_profiles)} profiles and started warmup")
                    except Exception as bulk_error:
                        logger.warning(f"Bulk warmup failed, trying individual tasks: {bulk_error}")
                        # Fallback: start individual warmup tasks
                        for profile_id in profile_ids:
                            try:
                                task_result = warmup_profile_task.delay(profile_id, duration_minutes=30)
                                warmup_task_ids.append(task_result.id)
                            except Exception as single_error:
                                logger.error(f"Failed to start warmup for profile {profile_id}: {single_error}")

                        if warmup_task_ids:
                            warmup_started = True
                            logger.info(f"Auto-started {len(warmup_task_ids)} individual warmup tasks")
                            await manager.broadcast(f"Bulk created {len(created_profiles)} profiles and started {len(warmup_task_ids)} warmup tasks")
                else:
                    logger.warning(f"Celery not available, warmup simulation mode for {len(profile_ids)} profiles")
                    warmup_started = "simulation"
                    await manager.broadcast(f"Bulk created {len(created_profiles)} profiles (warmup simulation)")

            except Exception as warmup_error:
                logger.error(f"Failed to start warmup for bulk created profiles: {warmup_error}")
                # Revert warmup status but keep the profiles
                for profile in created_profiles:
                    profile.status = "created"
                db.commit()
                warmup_started = False
                await manager.broadcast(f"Bulk created {len(created_profiles)} profiles (warmup failed)")
        elif not auto_start_warmup:
            await manager.broadcast(f"Bulk created {len(created_profiles)} profiles (no warmup)")

        return {
            "message": "Profiles created successfully",
            "created_count": len(created_profiles),
            "profiles": [profile.to_dict() for profile in created_profiles],
            "warmup_started": warmup_started,
            "warmup_task_ids": warmup_task_ids,
            "auto_start_warmup": auto_start_warmup
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in bulk profile creation: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create profiles: {str(e)}")


@app.post("/api/profiles-bulk-warmup")
async def bulk_warmup_profiles(db: Session = Depends(get_db)):
    """Start warmup for all non-warmed profiles."""
    try:
        # Find profiles that need warmup using optimized method
        profiles_to_warmup = BrowserProfile.get_profiles_for_warmup(db)

        if not profiles_to_warmup:
            return {
                "message": "No profiles need warmup",
                "started_count": 0,
                "profile_ids": []
            }

        started_count = 0
        profile_ids = []
        task_results = []

        for profile in profiles_to_warmup:
            # Update profile status to warming_up
            profile.status = "warming_up"
            profile_ids.append(profile.id)
            started_count += 1

        db.commit()

        # Start actual warmup tasks
        if CELERY_AVAILABLE:
            try:
                # Use the multiple profiles task for better efficiency
                task_result = warmup_multiple_profiles_task.delay(profile_ids, duration_minutes=30)
                task_results.append({
                    "task_id": task_result.id,
                    "profile_ids": profile_ids
                })
                logger.info(f"Started Celery warmup task {task_result.id} for {started_count} profiles")
            except Exception as e:
                logger.error(f"Failed to start Celery warmup task: {e}")
                # Fallback: update profiles to error status
                for profile in profiles_to_warmup:
                    profile.status = "error"
                db.commit()
                raise HTTPException(status_code=500, detail=f"Failed to start warmup tasks: {str(e)}")
        else:
            # Simulation mode - just update status after delay
            logger.warning("Running warmup in simulation mode (Celery not available)")
            import asyncio
            import random

            async def simulate_warmup():
                await asyncio.sleep(2)  # Simulate some processing time
                with get_db_session() as sim_db:
                    for profile_id in profile_ids:
                        profile = sim_db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
                        if profile:
                            profile.status = "warmed"
                            profile.warmup_completed = True
                            profile.warmup_sessions_count += 1
                            profile.warmup_time_spent += random.randint(25, 35)
                    sim_db.commit()

            # Start simulation in background
            asyncio.create_task(simulate_warmup())

        await manager.broadcast(f"Started warmup for {started_count} profiles")

        return {
            "message": "Bulk warmup started successfully",
            "started_count": started_count,
            "profile_ids": profile_ids,
            "celery_available": CELERY_AVAILABLE,
            "task_results": task_results if CELERY_AVAILABLE else None
        }

    except Exception as e:
        logger.error(f"Error in bulk warmup: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to start bulk warmup: {str(e)}")


@app.get("/api/profiles-overall-progress")
async def get_overall_progress(db: Session = Depends(get_db)):
    """Get overall progress statistics for all profiles."""
    try:
        # Use optimized single-query method for better performance with large datasets
        stats = BrowserProfile.get_warmup_stats(db)

        total_profiles = stats["total_profiles"]
        warmed_profiles = stats["warmed_profiles"]
        warming_profiles = stats["warming_profiles"]
        active_profiles = stats["active_profiles"]

        # Calculate different progress metrics
        warmup_percentage = round((warmed_profiles / max(total_profiles, 1)) * 100, 1)
        active_warmup_percentage = round((warmed_profiles / max(active_profiles, 1)) * 100, 1) if active_profiles > 0 else 0

        # Determine overall status
        if total_profiles == 0:
            status = "no_profiles"
        elif warming_profiles > 0:
            status = "in_progress"
        elif warmed_profiles == total_profiles:
            status = "completed"
        elif warmed_profiles == 0:
            status = "not_started"
        else:
            status = "partial"

        return {
            "total_profiles": total_profiles,
            "warmed_profiles": warmed_profiles,
            "warming_profiles": warming_profiles,
            "active_profiles": active_profiles,
            "pending_profiles": total_profiles - warmed_profiles - warming_profiles,
            "warmup_percentage": warmup_percentage,
            "active_warmup_percentage": active_warmup_percentage,
            "status": status,
            "can_start_bulk_warmup": (total_profiles - warmed_profiles - warming_profiles) > 0,
            "estimated_time_remaining": max(0, warming_profiles * 30) if warming_profiles > 0 else 0  # 30 min per profile estimate
        }
    except Exception as e:
        logger.error(f"Error getting overall progress: {e}")
        raise HTTPException(status_code=500, detail="Failed to get overall progress")


@app.post("/api/profiles/{profile_id}/start-warmup")
async def start_profile_warmup(profile_id: int, db: Session = Depends(get_db)):
    """Start warmup process for a specific profile."""
    try:
        profile = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")

        if profile.warmup_completed:
            raise HTTPException(status_code=400, detail="Profile already warmed up")

        if profile.status == "warming_up":
            raise HTTPException(status_code=400, detail="Profile is already warming up")

        # Update profile status
        profile.status = "warming_up"
        db.commit()

        # Start actual warmup task
        task_result = None
        if CELERY_AVAILABLE:
            try:
                task_result = warmup_profile_task.delay(profile_id, duration_minutes=30)
                logger.info(f"Started Celery warmup task {task_result.id} for profile {profile_id}")
            except Exception as e:
                logger.error(f"Failed to start Celery warmup task for profile {profile_id}: {e}")
                # Revert status on error
                profile.status = "error"
                db.commit()
                raise HTTPException(status_code=500, detail=f"Failed to start warmup task: {str(e)}")
        else:
            # Simulation mode - just update status after delay
            logger.warning(f"Running warmup in simulation mode for profile {profile_id} (Celery not available)")
            import asyncio
            import random

            async def simulate_single_warmup():
                await asyncio.sleep(1)  # Simulate some processing time
                with get_db() as sim_db:
                    sim_profile = sim_db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
                    if sim_profile:
                        sim_profile.status = "warmed"
                        sim_profile.warmup_completed = True
                        sim_profile.warmup_sessions_count += 1
                        sim_profile.warmup_time_spent += random.randint(25, 35)
                    sim_db.commit()

            # Start simulation in background
            asyncio.create_task(simulate_single_warmup())

        await manager.broadcast(f"Warmup started for profile: {profile.name}")

        return {
            "message": "Warmup started successfully",
            "profile_id": profile_id,
            "profile_name": profile.name,
            "estimated_duration_minutes": 30,
            "celery_available": CELERY_AVAILABLE,
            "task_id": task_result.id if task_result else None
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting warmup for profile {profile_id}: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to start warmup: {str(e)}")


@app.get("/api/profiles/{profile_id}/warmup-status")
async def get_warmup_status(profile_id: int, db: Session = Depends(get_db)):
    """Get current warmup status for a profile."""
    try:
        profile = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")

        # Calculate warmup progress (0-100%)
        session_progress = min((profile.warmup_sessions_count / 5) * 50, 50)  # 50% for sessions
        time_progress = min((profile.warmup_time_spent / 30) * 50, 50)  # 50% for time
        total_progress = int(session_progress + time_progress)

        # Determine if profile is ready for tasks
        is_ready = profile.warmup_sessions_count >= 5 and profile.warmup_time_spent >= 30

        return {
            "profile_id": profile_id,
            "status": profile.status,
            "warmup_completed": profile.warmup_completed,
            "warmup_sessions_count": profile.warmup_sessions_count,
            "warmup_time_spent": profile.warmup_time_spent,
            "progress_percentage": total_progress,
            "is_ready_for_tasks": is_ready,
            "session_progress": int(session_progress),
            "time_progress": int(time_progress),
            "last_used_at": profile.last_used_at.isoformat() if profile.last_used_at else None
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting warmup status for profile {profile_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get warmup status: {str(e)}")


@app.get("/api/profiles-warmup-progress")
async def get_all_warmup_progress(db: Session = Depends(get_db)):
    """Get warmup progress for all profiles."""
    try:
        profiles = db.query(BrowserProfile).all()
        progress_data = {}

        for profile in profiles:
            # Calculate warmup progress (0-100%)
            session_progress = min((profile.warmup_sessions_count / 5) * 50, 50)  # 50% for sessions
            time_progress = min((profile.warmup_time_spent / 30) * 50, 50)  # 50% for time
            total_progress = int(session_progress + time_progress)

            # Determine if profile is ready for tasks
            is_ready = profile.warmup_sessions_count >= 5 and profile.warmup_time_spent >= 30

            progress_data[profile.id] = {
                "status": profile.status,
                "warmup_completed": profile.warmup_completed,
                "warmup_sessions_count": profile.warmup_sessions_count,
                "warmup_time_spent": profile.warmup_time_spent,
                "progress_percentage": total_progress,
                "is_ready_for_tasks": is_ready,
                "session_progress": int(session_progress),
                "time_progress": int(time_progress)
            }

        return progress_data

    except Exception as e:
        logger.error(f"Error getting warmup progress for all profiles: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get warmup progress: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to start warmup: {str(e)}")


# Proxy Servers API
@app.get("/api/proxies", response_model=List[Dict[str, Any]])
async def get_proxies(db: Session = Depends(get_db)):
    """Get all proxy servers."""
    try:
        proxies = db.query(ProxyServer).all()
        return [proxy.to_dict() for proxy in proxies]
    except Exception as e:
        logger.error(f"Error getting proxies: {e}")
        raise HTTPException(status_code=500, detail="Failed to get proxies")


@app.post("/api/proxies")
async def create_proxy(proxy_data: Dict[str, Any], db: Session = Depends(get_db)):
    """Create a new proxy server."""
    try:
        proxy = ProxyServer(
            name=proxy_data.get("name", "New Proxy"),
            host=proxy_data["host"],
            port=proxy_data["port"],
            username=proxy_data.get("username"),
            password=proxy_data.get("password"),
            proxy_type=proxy_data.get("proxy_type", "http"),
            country=proxy_data.get("country"),
            city=proxy_data.get("city"),
            provider=proxy_data.get("provider")
        )
        db.add(proxy)
        db.commit()
        db.refresh(proxy)

        await manager.broadcast(f"New proxy added: {proxy.host}:{proxy.port}")
        return proxy.to_dict()

    except Exception as e:
        logger.error(f"Error creating proxy: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create proxy")


# Tasks API
@app.get("/api/tasks", response_model=List[Dict[str, Any]])
async def get_tasks(db: Session = Depends(get_db)):
    """Get all tasks."""
    try:
        tasks = db.query(Task).order_by(Task.created_at.desc()).limit(100).all()
        return [task.to_dict() for task in tasks]
    except Exception as e:
        logger.error(f"Error getting tasks: {e}")
        raise HTTPException(status_code=500, detail="Failed to get tasks")


@app.post("/api/tasks/warmup")
async def create_warmup_task(task_data: Dict[str, Any], db: Session = Depends(get_db)):
    """Create a warmup task."""
    try:
        task = Task.create_warmup_task(
            profile_id=task_data["profile_id"],
            name=task_data.get("name"),
            parameters=task_data.get("parameters", {})
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        await manager.broadcast(f"Warmup task created for profile #{task.profile_id}")
        return task.to_dict()

    except Exception as e:
        logger.error(f"Error creating warmup task: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create warmup task")


@app.post("/api/tasks/yandex-visit")
async def create_yandex_visit_task(task_data: Dict[str, Any], db: Session = Depends(get_db)):
    """Create a Yandex Maps visit task."""
    try:
        task = Task.create_yandex_visit_task(
            profile_id=task_data["profile_id"],
            target_url=task_data["target_url"],
            name=task_data.get("name"),
            parameters=task_data.get("parameters", {})
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        await manager.broadcast(f"Yandex visit task created for profile #{task.profile_id}")
        return task.to_dict()

    except Exception as e:
        logger.error(f"Error creating Yandex visit task: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create Yandex visit task")


# Settings API
@app.get("/api/settings", response_model=List[Dict[str, Any]])
async def get_settings(db: Session = Depends(get_db)):
    """Get all settings."""
    try:
        settings_list = db.query(UserSettings).all()
        return [setting.to_dict() for setting in settings_list]
    except Exception as e:
        logger.error(f"Error getting settings: {e}")
        raise HTTPException(status_code=500, detail="Failed to get settings")


@app.put("/api/settings/{setting_key}")
async def update_setting(setting_key: str, setting_data: Dict[str, Any], db: Session = Depends(get_db)):
    """Update a setting."""
    try:
        setting = db.query(UserSettings).filter(UserSettings.setting_key == setting_key).first()
        if not setting:
            raise HTTPException(status_code=404, detail="Setting not found")

        setting.set_typed_value(setting_data["value"])
        db.commit()

        await manager.broadcast(f"Setting updated: {setting_key}")
        return setting.to_dict()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating setting {setting_key}: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update setting")


# WebSocket endpoint
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates."""
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            await manager.send_personal_message(f"Message: {data}", websocket)
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# Dashboard API
@app.get("/api/dashboard/stats")
async def get_dashboard_stats(db: Session = Depends(get_db)):
    """Get dashboard statistics."""
    try:
        profile_count = db.query(BrowserProfile).count()
        active_profiles = db.query(BrowserProfile).filter(BrowserProfile.is_active == True).count()
        proxy_count = db.query(ProxyServer).count()
        working_proxies = db.query(ProxyServer).filter(ProxyServer.is_working == True).count()
        total_tasks = db.query(Task).count()
        completed_tasks = db.query(Task).filter(Task.status == "completed").count()

        return {
            "profiles": {
                "total": profile_count,
                "active": active_profiles
            },
            "proxies": {
                "total": proxy_count,
                "working": working_proxies
            },
            "tasks": {
                "total": total_tasks,
                "completed": completed_tasks,
                "success_rate": (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0
            }
        }
    except Exception as e:
        logger.error(f"Error getting dashboard stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to get dashboard stats")


# Domain Management API
@app.get("/api/domains/stats")
async def get_domain_stats():
    """Get domain statistics."""
    try:
        if not DOMAINS_AVAILABLE:
            raise HTTPException(status_code=503, detail="Domain manager not available")

        stats = domain_manager.get_stats()
        return {
            "domain_stats": stats,
            "files_loaded": True,
            "domains_available": DOMAINS_AVAILABLE
        }
    except Exception as e:
        logger.error(f"Error getting domain stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to get domain stats")


@app.get("/api/domains/preview/{profile_id}")
async def preview_domains_for_profile(profile_id: int, count: int = 15):
    """Preview domains that would be selected for a profile."""
    try:
        if not DOMAINS_AVAILABLE:
            raise HTTPException(status_code=503, detail="Domain manager not available")

        # Получаем превью доменов (без записи в историю)
        domains = domain_manager.get_random_domains_for_profile(
            profile_id=profile_id,
            count=count,
            avoid_repeats=False  # Не записываем в историю для превью
        )

        return {
            "profile_id": profile_id,
            "domains": domains,
            "count": len(domains),
            "preview": True
        }
    except Exception as e:
        logger.error(f"Error getting domain preview for profile {profile_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get domain preview")


@app.post("/api/domains/reload")
async def reload_domains():
    """Reload domains from files."""
    try:
        if not DOMAINS_AVAILABLE:
            raise HTTPException(status_code=503, detail="Domain manager not available")

        domain_manager.reload_domains()
        stats = domain_manager.get_stats()

        return {
            "message": "Domains reloaded successfully",
            "stats": stats,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Error reloading domains: {e}")
        raise HTTPException(status_code=500, detail="Failed to reload domains")


@app.post("/api/domains/reset-history")
async def reset_domain_history(profile_id: int = None):
    """Reset domain usage history for a profile or all profiles."""
    try:
        if not DOMAINS_AVAILABLE:
            raise HTTPException(status_code=503, detail="Domain manager not available")

        if profile_id is not None:
            domain_manager.reset_profile_history(profile_id)
            message = f"Domain history reset for profile {profile_id}"
        else:
            domain_manager.reset_all_history()
            message = "Domain history reset for all profiles"

        return {
            "message": message,
            "profile_id": profile_id,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Error resetting domain history: {e}")
        raise HTTPException(status_code=500, detail="Failed to reset domain history")


@app.get("/api/domains/categories")
async def get_domains_by_categories(categories: str = "social,news,search", count: int = 15):
    """Get domains by categories."""
    try:
        if not DOMAINS_AVAILABLE:
            raise HTTPException(status_code=503, detail="Domain manager not available")

        category_list = [cat.strip() for cat in categories.split(',')]
        domains = domain_manager.get_domains_by_category(category_list, count)

        return {
            "categories": category_list,
            "domains": domains,
            "count": len(domains)
        }
    except Exception as e:
        logger.error(f"Error getting domains by categories: {e}")
        raise HTTPException(status_code=500, detail="Failed to get domains by categories")


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.api_reload
    )