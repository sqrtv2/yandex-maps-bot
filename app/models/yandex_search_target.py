"""
Yandex Search Target model for managing search click-through tasks.
Simulates organic traffic: search keyword on Yandex → click through to target site.
"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float, Text
from datetime import datetime

from app.database import Base


class YandexSearchTarget(Base):
    """Model for Yandex Search click-through targets."""
    
    __tablename__ = "yandex_search_targets"

    id = Column(Integer, primary_key=True, index=True)
    
    # Target site
    domain = Column(String(500), nullable=False, index=True)  # e.g. "benesque.ru"
    
    # Keywords (one per line, stored as text)
    keywords = Column(Text, nullable=False)  # newline-separated keywords
    
    # Display
    title = Column(String(500), nullable=True)  # Optional description
    
    # Visit Configuration
    visits_per_day = Column(Integer, default=10)
    min_interval_minutes = Column(Integer, default=30)
    max_interval_minutes = Column(Integer, default=120)
    
    # Search behavior
    max_search_pages = Column(Integer, default=3)  # How deep to search (pages 1-N)
    min_time_on_site = Column(Integer, default=30)  # Min seconds on target site
    max_time_on_site = Column(Integer, default=120)  # Max seconds on target site
    
    # Concurrency
    concurrent_visits = Column(Integer, default=1)
    
    # Status & Statistics (cumulative)
    is_active = Column(Boolean, default=True)
    total_visits = Column(Integer, default=0)
    successful_visits = Column(Integer, default=0)
    failed_visits = Column(Integer, default=0)
    not_found_count = Column(Integer, default=0)  # Site not found in search results
    last_visit_at = Column(DateTime, nullable=True)

    # Daily statistics
    today_visits = Column(Integer, default=0)
    today_successful = Column(Integer, default=0)
    today_failed = Column(Integer, default=0)
    stats_reset_date = Column(DateTime, nullable=True)
    
    # Priority
    priority = Column(Integer, default=5)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Notes
    notes = Column(Text, nullable=True)

    def __repr__(self):
        return f"<YandexSearchTarget(id={self.id}, domain={self.domain}, visits_per_day={self.visits_per_day})>"

    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "domain": self.domain,
            "keywords": self.keywords,
            "keywords_list": self.get_keywords_list(),
            "keywords_count": len(self.get_keywords_list()),
            "title": self.title,
            "visits_per_day": self.visits_per_day,
            "min_interval_minutes": self.min_interval_minutes,
            "max_interval_minutes": self.max_interval_minutes,
            "max_search_pages": self.max_search_pages,
            "min_time_on_site": self.min_time_on_site,
            "max_time_on_site": self.max_time_on_site,
            "concurrent_visits": self.concurrent_visits,
            "is_active": self.is_active,
            "total_visits": self.total_visits,
            "successful_visits": self.successful_visits,
            "failed_visits": self.failed_visits,
            "not_found_count": self.not_found_count,
            "today_visits": self.today_visits or 0,
            "today_successful": self.today_successful or 0,
            "today_failed": self.today_failed or 0,
            "stats_reset_date": self.stats_reset_date.isoformat() if self.stats_reset_date else None,
            "last_visit_at": self.last_visit_at.isoformat() if self.last_visit_at else None,
            "priority": self.priority,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "notes": self.notes
        }

    def get_keywords_list(self):
        """Get keywords as a clean list."""
        if not self.keywords:
            return []
        return [k.strip() for k in self.keywords.strip().split('\n') if k.strip()]

    @property
    def success_rate(self) -> float:
        if self.total_visits == 0:
            return 0.0
        return (self.successful_visits / self.total_visits) * 100

    def should_visit_now(self, current_time=None) -> tuple:
        if not current_time:
            current_time = datetime.utcnow()
        if not self.is_active:
            return False, "Target is not active"
        if not self.last_visit_at:
            return True, "Never visited before"
        time_since_last = (current_time - self.last_visit_at).total_seconds() / 60
        if time_since_last < (self.min_interval_minutes - 0.5):
            return False, f"Too soon ({time_since_last:.1f} min ago, need {self.min_interval_minutes})"
        return True, "Ready for next visit"

    def get_visits_needed_now(self, current_time=None) -> int:
        if not current_time:
            current_time = datetime.utcnow()
        if not self.last_visit_at:
            return 1
        time_since_last = (current_time - self.last_visit_at).total_seconds() / 60
        if time_since_last < (self.min_interval_minutes - 0.5):
            return 0
        avg_interval = (24 * 60) / max(self.visits_per_day, 1)
        visits_should_have_done = int(time_since_last / avg_interval)
        return min(max(1, visits_should_have_done), self.concurrent_visits)
