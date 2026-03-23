"""
Search Position History model.
Tracks position of the target domain in Yandex search results for each keyword over time.
Used for analytics: growth/decline trends, strategy recommendations.
"""
from sqlalchemy import Column, Integer, String, DateTime, Float, ForeignKey, Boolean
from datetime import datetime

from app.database import Base


class SearchPositionHistory(Base):
    """Stores every position check result for analytics tracking.
    
    Each record = one search attempt result:
    - keyword searched
    - which page/position the domain was found (or not found)
    - timestamp for trend analysis
    """
    
    __tablename__ = "search_position_history"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # Link to search target
    search_target_id = Column(Integer, ForeignKey("yandex_search_targets.id"), nullable=False, index=True)
    
    # Search details
    keyword = Column(String(500), nullable=False, index=True)
    domain = Column(String(500), nullable=False)
    
    # Position data
    found = Column(Boolean, default=False)  # Was the domain found in search results?
    page = Column(Integer, nullable=True)  # Page number where found (1-based)
    position = Column(Integer, nullable=True)  # Position on page (1-based)
    absolute_position = Column(Integer, nullable=True)  # Overall position (page*10 + pos_on_page)
    
    # Context
    profile_id = Column(Integer, nullable=True)  # Which profile was used
    task_id = Column(Integer, nullable=True)  # Related task ID
    clicked = Column(Boolean, default=False)  # Was link actually clicked?
    browse_time = Column(Float, nullable=True)  # Time spent on site (seconds)
    referrer_used = Column(Boolean, default=False)  # Was referrer site visited before search?
    
    # Timestamp
    checked_at = Column(DateTime, default=datetime.utcnow, index=True)
    
    def __repr__(self):
        pos_str = f"p{self.page}#{self.position}" if self.found else "NOT_FOUND"
        return f"<SearchPositionHistory {self.keyword} → {self.domain} {pos_str} at {self.checked_at}>"
    
    def to_dict(self):
        return {
            "id": self.id,
            "search_target_id": self.search_target_id,
            "keyword": self.keyword,
            "domain": self.domain,
            "found": self.found,
            "page": self.page,
            "position": self.position,
            "absolute_position": self.absolute_position,
            "profile_id": self.profile_id,
            "task_id": self.task_id,
            "clicked": self.clicked,
            "browse_time": self.browse_time,
            "referrer_used": self.referrer_used,
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
        }
