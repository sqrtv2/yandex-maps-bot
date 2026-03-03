"""
Session-based authentication for the web interface.
Sessions are stored in Redis so they survive app container restarts.
Falls back to in-memory store if Redis is unavailable.
"""
import os
import hashlib
import json
import secrets
import time
import logging
from functools import wraps
from typing import Optional

from fastapi import Request, Response
from fastapi.responses import RedirectResponse

logger = logging.getLogger(__name__)

# Auth configuration
AUTH_USERNAME = os.environ.get("AUTH_USERNAME", "admin")
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "admin123")
SESSION_COOKIE_NAME = "session_token"
SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days
SESSION_REDIS_PREFIX = "web_session:"

# In-memory fallback session store
_sessions: dict = {}

# Redis connection (lazy init)
_redis_client = None
_redis_available = None  # None = not checked yet


def _get_redis():
    """Get Redis client, lazy-init on first use."""
    global _redis_client, _redis_available
    if _redis_available is False:
        return None
    if _redis_client is not None:
        return _redis_client
    try:
        import redis
        redis_host = os.environ.get("YANDEX_BOT_REDIS_HOST", "localhost")
        redis_port = int(os.environ.get("YANDEX_BOT_REDIS_PORT", "6379"))
        _redis_client = redis.Redis(host=redis_host, port=redis_port, db=0, decode_responses=True)
        _redis_client.ping()
        _redis_available = True
        logger.info(f"Auth sessions: using Redis ({redis_host}:{redis_port})")
        return _redis_client
    except Exception as e:
        _redis_available = False
        logger.warning(f"Auth sessions: Redis unavailable ({e}), using in-memory store")
        return None


def _hash_password(password: str) -> str:
    """Hash password for comparison."""
    return hashlib.sha256(password.encode()).hexdigest()


def create_session(username: str) -> str:
    """Create a new session and return token."""
    token = secrets.token_urlsafe(48)
    session_data = {
        "username": username,
        "created_at": time.time(),
    }

    r = _get_redis()
    if r:
        try:
            r.setex(
                f"{SESSION_REDIS_PREFIX}{token}",
                SESSION_MAX_AGE,
                json.dumps(session_data),
            )
            logger.info(f"Session created in Redis for user: {username}")
            return token
        except Exception as e:
            logger.warning(f"Redis session create failed ({e}), falling back to memory")

    _sessions[token] = session_data
    logger.info(f"Session created in memory for user: {username}")
    return token


def validate_session(token: Optional[str]) -> bool:
    """Check if session token is valid and not expired."""
    if not token:
        return False

    # Try Redis first
    r = _get_redis()
    if r:
        try:
            data = r.get(f"{SESSION_REDIS_PREFIX}{token}")
            if data:
                return True
            # Not found in Redis — check in-memory as migration fallback
        except Exception as e:
            logger.warning(f"Redis session validate failed: {e}")

    # Fallback to in-memory
    if token not in _sessions:
        return False
    session = _sessions[token]
    if time.time() - session["created_at"] > SESSION_MAX_AGE:
        del _sessions[token]
        return False
    return True


def destroy_session(token: Optional[str]):
    """Remove session."""
    if not token:
        return

    r = _get_redis()
    if r:
        try:
            r.delete(f"{SESSION_REDIS_PREFIX}{token}")
        except Exception:
            pass

    if token in _sessions:
        del _sessions[token]


def get_session_user(request: Request) -> Optional[str]:
    """Get username from session cookie."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None

    # Try Redis first
    r = _get_redis()
    if r:
        try:
            data = r.get(f"{SESSION_REDIS_PREFIX}{token}")
            if data:
                session = json.loads(data)
                return session.get("username")
        except Exception as e:
            logger.warning(f"Redis session lookup failed: {e}")

    # Fallback to in-memory
    if token in _sessions:
        session = _sessions[token]
        if time.time() - session["created_at"] <= SESSION_MAX_AGE:
            return session.get("username")
        else:
            del _sessions[token]

    return None


def is_authenticated(request: Request) -> bool:
    """Check if the request has a valid session."""
    return get_session_user(request) is not None


def check_credentials(username: str, password: str) -> bool:
    """Verify login credentials."""
    return username == AUTH_USERNAME and password == AUTH_PASSWORD


# Public paths that don't require auth
PUBLIC_PATHS = {"/login", "/health", "/api/health"}


def requires_auth(request: Request) -> Optional[RedirectResponse]:
    """
    Check if request requires auth redirect.
    Returns RedirectResponse if not authenticated, None if OK.
    """
    path = request.url.path
    
    # Skip auth for public paths and static files
    if path in PUBLIC_PATHS or path.startswith("/static"):
        return None
    
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=302)
    
    return None
