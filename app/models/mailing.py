"""
Mailing models for email campaigns using collected company contacts.
"""
from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, Enum, Index
from datetime import datetime
import enum

from app.database import Base


class MailingStatus(str, enum.Enum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class MessageStatus(str, enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


class SmtpAccount(Base):
    """SMTP account for sending emails (e.g. Yandex, Gmail, Mail.ru)."""

    __tablename__ = "smtp_accounts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    email = Column(String(500), nullable=False, unique=True)
    password = Column(String(500), nullable=False)
    smtp_server = Column(String(200), nullable=False, default="smtp.yandex.ru")
    smtp_port = Column(Integer, nullable=False, default=465)
    use_ssl = Column(Boolean, default=True)
    daily_limit = Column(Integer, default=450)
    sent_today = Column(Integer, default=0)
    last_reset_date = Column(String(10), nullable=True)
    is_active = Column(Boolean, default=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'email': self.email,
            'smtp_server': self.smtp_server,
            'smtp_port': self.smtp_port,
            'use_ssl': self.use_ssl,
            'daily_limit': self.daily_limit,
            'sent_today': self.sent_today,
            'is_active': self.is_active,
            'last_error': self.last_error,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class MailingCampaign(Base):
    """Email mailing campaign."""

    __tablename__ = "mailing_campaigns"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(500), nullable=False)
    subject = Column(String(1000), nullable=False)
    body_html = Column(Text, nullable=False)
    body_text = Column(Text, nullable=True)  # Plain text fallback
    sender_name = Column(String(200), nullable=True)

    # Filters for selecting companies
    filter_search_query = Column(String(500), nullable=True)
    filter_region = Column(String(200), nullable=True)
    filter_category = Column(String(500), nullable=True)
    filter_has_email = Column(Boolean, default=True)

    # Campaign settings
    delay_min = Column(Integer, default=30)   # Min delay between emails (seconds)
    delay_max = Column(Integer, default=90)   # Max delay between emails (seconds)
    status = Column(String(20), default=MailingStatus.DRAFT.value, index=True)

    # Stats
    total_recipients = Column(Integer, default=0)
    sent_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    celery_task_id = Column(String(200), nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'subject': self.subject,
            'body_html': self.body_html,
            'body_text': self.body_text,
            'sender_name': self.sender_name,
            'filter_search_query': self.filter_search_query,
            'filter_region': self.filter_region,
            'filter_category': self.filter_category,
            'filter_has_email': self.filter_has_email,
            'delay_min': self.delay_min,
            'delay_max': self.delay_max,
            'status': self.status,
            'total_recipients': self.total_recipients,
            'sent_count': self.sent_count,
            'failed_count': self.failed_count,
            'celery_task_id': self.celery_task_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
        }


class MailingMessage(Base):
    """Individual email message within a campaign."""

    __tablename__ = "mailing_messages"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, nullable=False, index=True)
    company_id = Column(Integer, nullable=False, index=True)
    smtp_account_id = Column(Integer, nullable=True)

    recipient_email = Column(String(500), nullable=False)
    company_name = Column(String(500), nullable=True)
    status = Column(String(20), default=MessageStatus.PENDING.value, index=True)
    error_message = Column(Text, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index('idx_message_campaign_status', 'campaign_id', 'status'),
        Index('idx_message_recipient', 'recipient_email'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'campaign_id': self.campaign_id,
            'company_id': self.company_id,
            'smtp_account_id': self.smtp_account_id,
            'recipient_email': self.recipient_email,
            'company_name': self.company_name,
            'status': self.status,
            'error_message': self.error_message,
            'sent_at': self.sent_at.isoformat() if self.sent_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
