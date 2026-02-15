"""
Browser Profile model for storing browser configurations and fingerprints.
"""
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, Float, JSON, Index
from datetime import datetime
import json

from app.database import Base


class BrowserProfile(Base):
    __tablename__ = "browser_profiles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)

    # Browser Configuration
    user_agent = Column(Text, nullable=False)
    viewport_width = Column(Integer, default=1366)
    viewport_height = Column(Integer, default=768)
    timezone = Column(String(100), default="Europe/Moscow")
    language = Column(String(50), default="ru-RU")
    platform = Column(String(50), default="Win32")

    # Fingerprinting Data
    canvas_fingerprint = Column(Text, nullable=True)
    webgl_fingerprint = Column(Text, nullable=True)
    audio_fingerprint = Column(Text, nullable=True)
    screen_fingerprint = Column(JSON, nullable=True)

    # Privacy Settings
    webrtc_leak_protect = Column(Boolean, default=True)
    geolocation_enabled = Column(Boolean, default=False)
    notifications_enabled = Column(Boolean, default=False)

    # Profile Status
    status = Column(String(50), default="created", index=True)  # created, warming_up, warmed, active, blocked
    is_active = Column(Boolean, default=True, index=True)
    warmup_completed = Column(Boolean, default=False, index=True)
    warmup_sessions_count = Column(Integer, default=0)
    warmup_time_spent = Column(Integer, default=0)  # in minutes

    # Proxy Settings
    proxy_host = Column(String(255), nullable=True)
    proxy_port = Column(Integer, nullable=True)
    proxy_username = Column(String(255), nullable=True)
    proxy_password = Column(String(255), nullable=True)
    proxy_type = Column(String(20), default="http")  # http, socks5

    # Usage Statistics
    total_sessions = Column(Integer, default=0)
    successful_sessions = Column(Integer, default=0)
    failed_sessions = Column(Integer, default=0)
    last_used_at = Column(DateTime, nullable=True)
    last_ip_address = Column(String(45), nullable=True)  # IPv4 or IPv6

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)

    # Additional Settings
    custom_headers = Column(JSON, nullable=True)
    cookies_data = Column(JSON, nullable=True)
    local_storage_data = Column(JSON, nullable=True)

    # Composite indexes for large-scale operations (1000+ profiles)
    __table_args__ = (
        # Index for bulk warmup queries (most common filter combination)
        Index('idx_warmup_filter', 'warmup_completed', 'is_active', 'status'),

        # Index for pagination with filtering by status
        Index('idx_status_created', 'status', 'created_at'),

        # Index for progress queries
        Index('idx_progress_stats', 'warmup_completed', 'status'),

        # Index for active profiles
        Index('idx_active_warmup', 'is_active', 'warmup_completed'),
    )

    def __repr__(self):
        return f"<BrowserProfile(id={self.id}, name='{self.name}', status='{self.status}')>"

    def to_dict(self):
        """Convert model to dictionary."""
        return {
            'id': self.id,
            'name': self.name,
            'user_agent': self.user_agent,
            'viewport_width': self.viewport_width,
            'viewport_height': self.viewport_height,
            'timezone': self.timezone,
            'language': self.language,
            'platform': self.platform,
            'status': self.status,
            'is_active': self.is_active,
            'warmup_completed': self.warmup_completed,
            'warmup_sessions_count': self.warmup_sessions_count,
            'warmup_time_spent': self.warmup_time_spent,
            'total_sessions': self.total_sessions,
            'successful_sessions': self.successful_sessions,
            'failed_sessions': self.failed_sessions,
            'last_used_at': self.last_used_at.isoformat() if self.last_used_at else None,
            'last_ip_address': self.last_ip_address,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'proxy_host': self.proxy_host,
            'proxy_port': self.proxy_port,
            'proxy_type': self.proxy_type
        }

    def update_session_stats(self, success: bool = True):
        """Update session statistics."""
        self.total_sessions += 1
        if success:
            self.successful_sessions += 1
        else:
            self.failed_sessions += 1
        self.last_used_at = datetime.utcnow()

    def get_success_rate(self) -> float:
        """Calculate success rate percentage."""
        if self.total_sessions == 0:
            return 0.0
        return (self.successful_sessions / self.total_sessions) * 100

    def is_ready_for_tasks(self) -> bool:
        """Check if profile is ready for Yandex Maps tasks."""
        return (
            self.status == "warmed" and
            self.warmup_completed and
            self.is_active
        )

    def can_start_warmup(self) -> bool:
        """Check if profile can start warmup process."""
        return (
            not self.warmup_completed and
            self.is_active and
            self.status in ["created", "active"]
        )

    @classmethod
    def get_warmup_stats(cls, db):
        """Get warmup statistics efficiently for large datasets."""
        from sqlalchemy import func, case

        # Single query to get all stats we need
        result = db.query(
            func.count(cls.id).label('total_profiles'),
            func.sum(case((cls.warmup_completed == True, 1), else_=0)).label('warmed_profiles'),
            func.sum(case((cls.status == 'warming_up', 1), else_=0)).label('warming_profiles'),
            func.sum(case((cls.is_active == True, 1), else_=0)).label('active_profiles')
        ).first()

        return {
            'total_profiles': int(result.total_profiles or 0),
            'warmed_profiles': int(result.warmed_profiles or 0),
            'warming_profiles': int(result.warming_profiles or 0),
            'active_profiles': int(result.active_profiles or 0)
        }

    @classmethod
    def get_profiles_for_warmup(cls, db, limit=None):
        """Get profiles that need warmup, optimized for bulk operations."""
        query = db.query(cls).filter(
            cls.warmup_completed == False,
            cls.is_active == True,
            cls.status.in_(["created", "active"])
        ).order_by(cls.created_at)  # Use indexed column for ordering

        if limit:
            query = query.limit(limit)

        return query.all()

    @classmethod
    def count_by_filters(cls, db, status=None, warmup_completed=None, is_active=None, search=None):
        """Efficiently count profiles with filters."""
        query = db.query(cls)

        if status:
            query = query.filter(cls.status == status)
        if warmup_completed is not None:
            query = query.filter(cls.warmup_completed == warmup_completed)
        if is_active is not None:
            query = query.filter(cls.is_active == is_active)
        if search:
            query = query.filter(cls.name.ilike(f"%{search}%"))

        return query.count()