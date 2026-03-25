from sqlalchemy import Column, Integer, String, DateTime, Text, Index
from datetime import datetime
from app.database import Base


class ErrorLog(Base):
    __tablename__ = "error_logs"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, nullable=True, index=True)
    task_type = Column(String(50), nullable=False, index=True)  # yandex_search, yandex_visit, warmup
    profile_id = Column(Integer, nullable=True, index=True)
    profile_name = Column(String(200), nullable=True)

    # Error classification
    error_category = Column(String(50), nullable=False, index=True)
    # Categories: captcha, renderer_death, click_failed, not_found, timeout,
    #             proxy_error, browser_crash, worker_killed, unknown

    error_message = Column(String(500), nullable=True)
    error_detail = Column(Text, nullable=True)  # Full stacktrace or extended info

    # Context
    keyword = Column(String(500), nullable=True)
    domain = Column(String(500), nullable=True)
    proxy_host = Column(String(200), nullable=True)
    proxy_id = Column(Integer, nullable=True)

    # Timing
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    task_duration_seconds = Column(Integer, nullable=True)

    __table_args__ = (
        Index('ix_error_logs_category_created', 'error_category', 'created_at'),
        Index('ix_error_logs_domain_created', 'domain', 'created_at'),
    )

    def __repr__(self):
        return f"<ErrorLog [{self.error_category}] task={self.task_id} {self.error_message[:50] if self.error_message else ''}>"

    def to_dict(self):
        return {
            "id": self.id,
            "task_id": self.task_id,
            "task_type": self.task_type,
            "profile_id": self.profile_id,
            "profile_name": self.profile_name,
            "error_category": self.error_category,
            "error_message": self.error_message,
            "error_detail": self.error_detail,
            "keyword": self.keyword,
            "domain": self.domain,
            "proxy_host": self.proxy_host,
            "proxy_id": self.proxy_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "task_duration_seconds": self.task_duration_seconds,
        }
