"""
Parse Task model for tracking Yandex Maps parsing jobs.
"""
from sqlalchemy import Column, Integer, String, DateTime, Text, Enum, Index
from datetime import datetime
import enum

from app.database import Base


class ParseTaskStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ParseTask(Base):
    """Tracks Yandex Maps parsing tasks."""
    
    __tablename__ = "parse_tasks"

    id = Column(Integer, primary_key=True, index=True)
    
    # Search parameters
    search_query = Column(String(500), nullable=False)
    region = Column(String(200), nullable=True, default="Москва")
    yandex_maps_url = Column(Text, nullable=True)
    max_items = Column(Integer, default=100)
    
    # Status
    status = Column(String(50), default=ParseTaskStatus.PENDING, index=True)
    celery_task_id = Column(String(200), nullable=True)
    
    # Progress
    items_found = Column(Integer, default=0)
    items_parsed = Column(Integer, default=0)
    items_saved = Column(Integer, default=0)
    
    # Results
    error_message = Column(Text, nullable=True)
    log = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'search_query': self.search_query,
            'region': self.region,
            'yandex_maps_url': self.yandex_maps_url,
            'max_items': self.max_items,
            'status': self.status,
            'celery_task_id': self.celery_task_id,
            'items_found': self.items_found,
            'items_parsed': self.items_parsed,
            'items_saved': self.items_saved,
            'error_message': self.error_message,
            'log': self.log,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
        }
    
    def add_log(self, message: str):
        """Append a log message with timestamp."""
        timestamp = datetime.utcnow().strftime("%H:%M:%S")
        entry = f"[{timestamp}] {message}"
        if self.log:
            self.log += "\n" + entry
        else:
            self.log = entry
