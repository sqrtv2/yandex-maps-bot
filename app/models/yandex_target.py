"""
Yandex Maps Target URL model for managing target URLs to visit.
"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float, Text
from datetime import datetime

from app.database import Base


class YandexMapTarget(Base):
    """Model for storing and managing Yandex Maps URLs to visit."""
    
    __tablename__ = "yandex_map_targets"

    id = Column(Integer, primary_key=True, index=True)
    
    # URL Information
    url = Column(String(2048), nullable=False, index=True)
    title = Column(String(500), nullable=True)  # Optional title/description
    organization_name = Column(String(500), nullable=True)  # Extracted from URL
    
    # Visit Configuration
    visits_per_day = Column(Integer, default=10)  # How many times to visit per day
    min_interval_minutes = Column(Integer, default=60)  # Minimum interval between visits
    max_interval_minutes = Column(Integer, default=180)  # Maximum interval between visits
    
    # Visit Parameters
    min_visit_duration = Column(Integer, default=120)  # Min time on page (seconds)
    max_visit_duration = Column(Integer, default=600)  # Max time on page (seconds)
    
    # Multi-threading Configuration
    concurrent_visits = Column(Integer, default=1)  # How many profiles can visit simultaneously
    use_different_profiles = Column(Boolean, default=True)  # Rotate through different profiles
    
    # Status & Statistics
    is_active = Column(Boolean, default=True)
    total_visits = Column(Integer, default=0)
    successful_visits = Column(Integer, default=0)
    failed_visits = Column(Integer, default=0)
    last_visit_at = Column(DateTime, nullable=True)
    
    # Priority & Scheduling
    priority = Column(Integer, default=5)  # 1-10, higher = more important
    schedule_type = Column(String(50), default="distributed")  # distributed, peak_hours, custom
    
    # Actions Configuration (JSON-like string)
    enabled_actions = Column(Text, default="scroll,photos,reviews,contacts,map")  # Comma-separated
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Notes
    notes = Column(Text, nullable=True)

    def __repr__(self):
        return f"<YandexMapTarget(id={self.id}, url={self.url[:50]}, visits_per_day={self.visits_per_day})>"

    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "organization_name": self.organization_name,
            "visits_per_day": self.visits_per_day,
            "min_interval_minutes": self.min_interval_minutes,
            "max_interval_minutes": self.max_interval_minutes,
            "min_visit_duration": self.min_visit_duration,
            "max_visit_duration": self.max_visit_duration,
            "concurrent_visits": self.concurrent_visits,
            "use_different_profiles": self.use_different_profiles,
            "is_active": self.is_active,
            "total_visits": self.total_visits,
            "successful_visits": self.successful_visits,
            "failed_visits": self.failed_visits,
            "last_visit_at": self.last_visit_at.isoformat() if self.last_visit_at else None,
            "priority": self.priority,
            "schedule_type": self.schedule_type,
            "enabled_actions": self.enabled_actions,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "notes": self.notes
        }

    @property
    def success_rate(self) -> float:
        """Calculate success rate percentage."""
        if self.total_visits == 0:
            return 0.0
        return (self.successful_visits / self.total_visits) * 100

    @property
    def visits_today_needed(self) -> int:
        """Calculate how many more visits needed today."""
        # This would need to check actual visits from today
        # For now, return the configured amount
        return self.visits_per_day
    
    def is_action_enabled(self, action: str) -> bool:
        """Check if specific action is enabled."""
        if not self.enabled_actions:
            return False
        return action in self.enabled_actions.split(',')
    
    def should_visit_now(self, current_time: datetime = None) -> tuple:
        """
        Check if this target should be visited now.
        
        Returns:
            (should_visit: bool, reason: str)
        """
        if not current_time:
            current_time = datetime.utcnow()
        
        if not self.is_active:
            return False, "Target is not active"
        
        # If never visited, should visit now
        if not self.last_visit_at:
            return True, "Never visited before"
        
        # Calculate time since last visit
        time_since_last = (current_time - self.last_visit_at).total_seconds() / 60  # minutes
        
        # Check if enough time has passed (use min interval)
        if time_since_last < self.min_interval_minutes:
            return False, f"Too soon (last visit {time_since_last:.1f} min ago)"
        
        return True, "Ready for next visit"
    
    def get_visits_needed_now(self, current_time: datetime = None) -> int:
        """
        Calculate how many visits should be scheduled now based on daily target.
        
        Returns:
            Number of visits to schedule
        """
        if not current_time:
            current_time = datetime.utcnow()
        
        # If never visited, schedule one visit
        if not self.last_visit_at:
            return 1
        
        # Calculate time since last visit
        time_since_last = (current_time - self.last_visit_at).total_seconds() / 60  # minutes
        
        # If within min interval, don't schedule
        if time_since_last < self.min_interval_minutes:
            return 0
        
        # Calculate average interval for this target (hours in a day / visits per day)
        avg_interval = (24 * 60) / self.visits_per_day  # minutes
        
        # Calculate how many visits we should have done by now
        visits_should_have_done = int(time_since_last / avg_interval)
        
        # Schedule at least 1, but respect concurrent_visits limit
        return min(max(1, visits_should_have_done), self.concurrent_visits)
