"""
Profile-Search Target Visit tracking model.
Tracks which browser profile has already clicked through to which Yandex Search target.
Each profile can click on each search target only once to look natural.
"""
from sqlalchemy import Column, Integer, DateTime, ForeignKey, UniqueConstraint, String
from datetime import datetime

from app.database import Base


class ProfileSearchVisit(Base):
    """Tracks completed search click-throughs: (profile_id, search_target_id) pairs.
    
    A profile that already has a record for a search target will not be
    selected for that target again — one profile clicks one site only once.
    """
    
    __tablename__ = "profile_search_visits"
    
    id = Column(Integer, primary_key=True, index=True)
    profile_id = Column(Integer, ForeignKey("browser_profiles.id"), nullable=False, index=True)
    search_target_id = Column(Integer, ForeignKey("yandex_search_targets.id"), nullable=False, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True)
    keyword = Column(String(500), nullable=True)  # which keyword was used
    status = Column(String(50), default="completed")  # completed, failed
    visited_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        UniqueConstraint("profile_id", "search_target_id", name="uq_profile_search_target"),
    )
    
    def __repr__(self):
        return f"<ProfileSearchVisit profile={self.profile_id} search_target={self.search_target_id} at={self.visited_at}>"
