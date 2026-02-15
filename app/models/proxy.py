"""
Proxy Server model for managing proxy configurations.
"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float, Text
from datetime import datetime, timedelta

from app.database import Base


class ProxyServer(Base):
    __tablename__ = "proxy_servers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)

    # Proxy Configuration
    host = Column(String(255), nullable=False, index=True)
    port = Column(Integer, nullable=False)
    username = Column(String(255), nullable=True)
    password = Column(String(255), nullable=True)
    proxy_type = Column(String(20), default="http")  # http, https, socks4, socks5

    # Location & Provider Info
    country = Column(String(100), nullable=True)
    city = Column(String(100), nullable=True)
    provider = Column(String(255), nullable=True)
    location = Column(String(255), nullable=True)  # Combined country/city

    # Status & Health
    is_active = Column(Boolean, default=True)
    is_working = Column(Boolean, default=True)
    status = Column(String(50), default="unchecked")  # unchecked, working, failed, banned

    # Performance Metrics
    response_time_ms = Column(Float, nullable=True)  # Average response time in milliseconds
    success_rate = Column(Float, default=0.0)  # Success rate percentage
    total_requests = Column(Integer, default=0)
    successful_requests = Column(Integer, default=0)
    failed_requests = Column(Integer, default=0)

    # Usage Statistics
    times_used = Column(Integer, default=0)
    last_used_at = Column(DateTime, nullable=True)
    last_check_at = Column(DateTime, nullable=True)
    last_success_at = Column(DateTime, nullable=True)
    last_failure_at = Column(DateTime, nullable=True)

    # Error Information
    last_error_message = Column(Text, nullable=True)
    consecutive_failures = Column(Integer, default=0)
    ban_until = Column(DateTime, nullable=True)  # Temporary ban timestamp

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Additional Settings
    max_concurrent_connections = Column(Integer, default=10)
    cooldown_seconds = Column(Integer, default=5)  # Cooldown between requests
    notes = Column(Text, nullable=True)

    def __repr__(self):
        return f"<ProxyServer(id={self.id}, host='{self.host}:{self.port}', status='{self.status}')>"

    def to_dict(self):
        """Convert model to dictionary."""
        return {
            'id': self.id,
            'name': self.name,
            'host': self.host,
            'port': self.port,
            'proxy_type': self.proxy_type,
            'country': self.country,
            'city': self.city,
            'provider': self.provider,
            'location': self.location,
            'is_active': self.is_active,
            'is_working': self.is_working,
            'status': self.status,
            'response_time_ms': self.response_time_ms,
            'success_rate': self.success_rate,
            'total_requests': self.total_requests,
            'successful_requests': self.successful_requests,
            'failed_requests': self.failed_requests,
            'times_used': self.times_used,
            'last_used_at': self.last_used_at.isoformat() if self.last_used_at else None,
            'last_check_at': self.last_check_at.isoformat() if self.last_check_at else None,
            'last_success_at': self.last_success_at.isoformat() if self.last_success_at else None,
            'last_failure_at': self.last_failure_at.isoformat() if self.last_failure_at else None,
            'last_error_message': self.last_error_message,
            'consecutive_failures': self.consecutive_failures,
            'ban_until': self.ban_until.isoformat() if self.ban_until else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'max_concurrent_connections': self.max_concurrent_connections,
            'cooldown_seconds': self.cooldown_seconds,
            'notes': self.notes
        }

    def get_proxy_url(self) -> str:
        """Get formatted proxy URL."""
        if self.username and self.password:
            return f"{self.proxy_type}://{self.username}:{self.password}@{self.host}:{self.port}"
        return f"{self.proxy_type}://{self.host}:{self.port}"

    def update_success(self, response_time_ms: float = None):
        """Update statistics after successful request."""
        self.total_requests += 1
        self.successful_requests += 1
        self.times_used += 1
        self.consecutive_failures = 0
        self.last_used_at = datetime.utcnow()
        self.last_success_at = datetime.utcnow()
        self.last_check_at = datetime.utcnow()
        self.status = "working"
        self.is_working = True

        if response_time_ms is not None:
            if self.response_time_ms is None:
                self.response_time_ms = response_time_ms
            else:
                # Calculate rolling average
                self.response_time_ms = (self.response_time_ms * 0.8) + (response_time_ms * 0.2)

        self._update_success_rate()

    def update_failure(self, error_message: str = None):
        """Update statistics after failed request. Proxy always stays working."""
        self.total_requests += 1
        self.failed_requests += 1
        self.consecutive_failures += 1
        self.last_failure_at = datetime.utcnow()
        self.last_check_at = datetime.utcnow()

        if error_message:
            self.last_error_message = error_message

        # Never ban or disable — proxy always stays available
        self.is_working = True
        self.status = "working"

        self._update_success_rate()

    def _update_success_rate(self):
        """Update success rate percentage."""
        if self.total_requests > 0:
            self.success_rate = (self.successful_requests / self.total_requests) * 100
        else:
            self.success_rate = 0.0

    def is_available(self) -> bool:
        """Check if proxy is available for use."""
        if not self.is_active:
            return False

        # Proxy is always available if active
        return True

    def needs_health_check(self) -> bool:
        """Check if proxy needs health check."""
        if self.status == "unchecked":
            return True

        if not self.last_check_at:
            return True

        # Check every 10 minutes
        return datetime.utcnow() - self.last_check_at > timedelta(minutes=10)

    def reset_ban(self):
        """Reset ban status."""
        self.ban_until = None
        self.consecutive_failures = 0
        self.status = "unchecked"
        self.is_working = True