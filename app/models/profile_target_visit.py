"""
Profile-Target Visit tracking model.
Tracks which browser profile has already visited which Yandex Maps target.
Each profile can visit each target only once.
"""
from sqlalchemy import Column, Integer, DateTime, ForeignKey, UniqueConstraint, String
from datetime import datetime

from app.database import Base


class ProfileTargetVisit(Base):
    """Tracks completed visits: (profile_id, target_id) pairs.
    
    A profile that already has a record for a target will not be
    selected for that target again.
    """
    
    __tablename__ = "profile_target_visits"
    
    id = Column(Integer, primary_key=True, index=True)
    profile_id = Column(Integer, ForeignKey("browser_profiles.id"), nullable=False, index=True)
    target_id = Column(Integer, ForeignKey("yandex_map_targets.id"), nullable=False, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True)
    status = Column(String(50), default="completed")  # completed, failed
    visited_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        UniqueConstraint("profile_id", "target_id", name="uq_profile_target"),
    )
    
    def __repr__(self):
        return f"<ProfileTargetVisit profile={self.profile_id} target={self.target_id} at={self.visited_at}>"
