"""
Task model for managing warmup and visit tasks.
"""
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, Float, JSON, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from enum import Enum
import json

from app.database import Base


class TaskType(str, Enum):
    WARMUP = "warmup"
    YANDEX_VISIT = "yandex_visit"
    YANDEX_SEARCH = "yandex_search"
    HEALTH_CHECK = "health_check"
    PROFILE_CREATION = "profile_creation"


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRY = "retry"


class TaskPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)

    # Task Configuration
    name = Column(String(255), nullable=False)
    task_type = Column(String(50), nullable=False, index=True)  # warmup, yandex_visit, etc.
    status = Column(String(50), default=TaskStatus.PENDING.value, index=True)
    priority = Column(String(20), default=TaskPriority.NORMAL.value)

    # Task Data
    target_url = Column(Text, nullable=True)  # URL for visit tasks
    profile_id = Column(Integer, ForeignKey('browser_profiles.id'), nullable=True, index=True)
    proxy_id = Column(Integer, ForeignKey('proxy_servers.id'), nullable=True)

    # Execution Parameters
    parameters = Column(JSON, nullable=True)  # Task-specific parameters
    max_retries = Column(Integer, default=3)
    retry_count = Column(Integer, default=0)
    timeout_seconds = Column(Integer, default=300)  # 5 minutes default timeout

    # Scheduling
    scheduled_at = Column(DateTime, nullable=True)  # When to execute
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    next_retry_at = Column(DateTime, nullable=True)

    # Results & Logs
    result = Column(JSON, nullable=True)  # Task execution result
    error_message = Column(Text, nullable=True)
    execution_logs = Column(Text, nullable=True)
    execution_time_seconds = Column(Float, nullable=True)

    # Worker Information
    worker_id = Column(String(255), nullable=True)  # Celery worker ID
    celery_task_id = Column(String(255), nullable=True, index=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships (these will work when models are properly connected)
    # profile = relationship("BrowserProfile", back_populates="tasks")
    # proxy = relationship("ProxyServer", back_populates="tasks")

    def __repr__(self):
        return f"<Task(id={self.id}, type='{self.task_type}', status='{self.status}')>"

    def to_dict(self):
        """Convert model to dictionary."""
        return {
            'id': self.id,
            'name': self.name,
            'task_type': self.task_type,
            'status': self.status,
            'priority': self.priority,
            'target_url': self.target_url,
            'profile_id': self.profile_id,
            'proxy_id': self.proxy_id,
            'parameters': self.parameters,
            'max_retries': self.max_retries,
            'retry_count': self.retry_count,
            'timeout_seconds': self.timeout_seconds,
            'scheduled_at': self.scheduled_at.isoformat() if self.scheduled_at else None,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'next_retry_at': self.next_retry_at.isoformat() if self.next_retry_at else None,
            'result': self.result,
            'error_message': self.error_message,
            'execution_time_seconds': self.execution_time_seconds,
            'worker_id': self.worker_id,
            'celery_task_id': self.celery_task_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

    def start_execution(self, worker_id: str = None, celery_task_id: str = None):
        """Mark task as started."""
        self.status = TaskStatus.IN_PROGRESS.value
        self.started_at = datetime.utcnow()
        if worker_id:
            self.worker_id = worker_id
        if celery_task_id:
            self.celery_task_id = celery_task_id

    def complete_successfully(self, result: dict = None, execution_time: float = None):
        """Mark task as completed successfully."""
        self.status = TaskStatus.COMPLETED.value
        self.completed_at = datetime.utcnow()
        if result:
            self.result = result
        if execution_time:
            self.execution_time_seconds = execution_time

    def fail_with_error(self, error_message: str, execution_time: float = None):
        """Mark task as failed with error."""
        self.error_message = error_message
        self.retry_count += 1

        if execution_time:
            self.execution_time_seconds = execution_time

        if self.retry_count >= self.max_retries:
            self.status = TaskStatus.FAILED.value
            self.completed_at = datetime.utcnow()
        else:
            self.status = TaskStatus.RETRY.value
            # Schedule retry in 5 minutes * retry_count (exponential backoff)
            from datetime import timedelta
            self.next_retry_at = datetime.utcnow() + timedelta(minutes=5 * self.retry_count)

    def cancel(self):
        """Cancel the task."""
        self.status = TaskStatus.CANCELLED.value
        self.completed_at = datetime.utcnow()

    def is_ready_for_execution(self) -> bool:
        """Check if task is ready for execution."""
        if self.status != TaskStatus.PENDING.value:
            return False

        if self.scheduled_at and self.scheduled_at > datetime.utcnow():
            return False

        return True

    def is_ready_for_retry(self) -> bool:
        """Check if task is ready for retry."""
        if self.status != TaskStatus.RETRY.value:
            return False

        if self.next_retry_at and self.next_retry_at > datetime.utcnow():
            return False

        return True

    def get_execution_duration(self) -> float:
        """Get task execution duration in seconds."""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return 0.0

    def add_log(self, message: str):
        """Add log entry to task."""
        timestamp = datetime.utcnow().isoformat()
        log_entry = f"[{timestamp}] {message}\n"

        if self.execution_logs:
            self.execution_logs += log_entry
        else:
            self.execution_logs = log_entry

    @classmethod
    def create_warmup_task(cls, profile_id: int, name: str = None, parameters: dict = None):
        """Create a warmup task for a profile."""
        if not name:
            name = f"Warmup Profile #{profile_id}"

        return cls(
            name=name,
            task_type=TaskType.WARMUP.value,
            profile_id=profile_id,
            parameters=parameters or {},
            priority=TaskPriority.NORMAL.value
        )

    @classmethod
    def create_yandex_visit_task(cls, profile_id: int, target_url: str, name: str = None, parameters: dict = None):
        """Create a Yandex Maps visit task."""
        if not name:
            name = f"Visit Yandex Maps Profile #{profile_id}"

        return cls(
            name=name,
            task_type=TaskType.YANDEX_VISIT.value,
            profile_id=profile_id,
            target_url=target_url,
            parameters=parameters or {},
            priority=TaskPriority.HIGH.value
        )

    @classmethod
    def create_health_check_task(cls, proxy_id: int, name: str = None):
        """Create a proxy health check task."""
        if not name:
            name = f"Health Check Proxy #{proxy_id}"

        return cls(
            name=name,
            task_type=TaskType.HEALTH_CHECK.value,
            proxy_id=proxy_id,
            priority=TaskPriority.LOW.value,
            timeout_seconds=60
        )