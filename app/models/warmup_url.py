from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from datetime import datetime
from app.database import Base


class WarmupUrl(Base):
    """Model for storing warmup URLs."""
    __tablename__ = "warmup_urls"

    id = Column(Integer, primary_key=True, index=True)
    url = Column(Text, nullable=False, unique=True, index=True)
    domain = Column(String(255), nullable=True, index=True)
    is_active = Column(Boolean, default=True, nullable=False)
    usage_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<WarmupUrl(id={self.id}, url={self.url[:50]}...)>"

    def to_dict(self):
        """Convert to dictionary."""
        return {
            'id': self.id,
            'url': self.url,
            'domain': self.domain,
            'is_active': self.is_active,
            'usage_count': self.usage_count,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

    def increment_usage(self):
        """Increment usage count."""
        self.usage_count += 1
        self.updated_at = datetime.utcnow()

    @staticmethod
    def extract_domain(url: str) -> str:
        """Extract domain from URL."""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            return parsed.netloc.lower()
        except Exception:
            return ""