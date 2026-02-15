"""
Session-based authentication for the web interface.
"""
import os
import hashlib
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

# In-memory session store (sufficient for single-instance app)
_sessions: dict = {}


def _hash_password(password: str) -> str:
    """Hash password for comparison."""
    return hashlib.sha256(password.encode()).hexdigest()


def create_session(username: str) -> str:
    """Create a new session and return token."""
    token = secrets.token_urlsafe(48)
    _sessions[token] = {
        "username": username,
        "created_at": time.time(),
    }
    logger.info(f"Session created for user: {username}")
    return token


def validate_session(token: Optional[str]) -> bool:
    """Check if session token is valid and not expired."""
    if not token or token not in _sessions:
        return False
    session = _sessions[token]
    if time.time() - session["created_at"] > SESSION_MAX_AGE:
        del _sessions[token]
        return False
    return True


def destroy_session(token: Optional[str]):
    """Remove session."""
    if token and token in _sessions:
        del _sessions[token]


def get_session_user(request: Request) -> Optional[str]:
    """Get username from session cookie."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token and validate_session(token):
        return _sessions[token]["username"]
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
