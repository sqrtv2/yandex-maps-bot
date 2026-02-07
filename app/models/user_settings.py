"""
User Settings model for system configuration.
"""
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, Float, JSON
from datetime import datetime
import json

from app.database import Base


class UserSettings(Base):
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, index=True)
    setting_key = Column(String(255), unique=True, nullable=False, index=True)
    setting_value = Column(Text, nullable=True)
    setting_type = Column(String(50), nullable=False, default="string")  # string, int, float, bool, json
    description = Column(Text, nullable=True)
    category = Column(String(100), nullable=True, index=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<UserSettings(key='{self.setting_key}', value='{self.setting_value}')>"

    def to_dict(self):
        """Convert model to dictionary."""
        return {
            'id': self.id,
            'setting_key': self.setting_key,
            'setting_value': self.get_typed_value(),
            'setting_type': self.setting_type,
            'description': self.description,
            'category': self.category,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

    def get_typed_value(self):
        """Get value converted to proper type."""
        if self.setting_value is None:
            return None

        try:
            if self.setting_type == "int":
                return int(self.setting_value)
            elif self.setting_type == "float":
                return float(self.setting_value)
            elif self.setting_type == "bool":
                return self.setting_value.lower() in ("true", "1", "yes", "on")
            elif self.setting_type == "json":
                return json.loads(self.setting_value)
            else:  # string
                return self.setting_value
        except (ValueError, json.JSONDecodeError):
            return self.setting_value  # Return as string if conversion fails

    def set_typed_value(self, value):
        """Set value with automatic type conversion."""
        if value is None:
            self.setting_value = None
        elif self.setting_type == "json":
            self.setting_value = json.dumps(value)
        else:
            self.setting_value = str(value)

    @classmethod
    def get_default_settings(cls):
        """Get default system settings."""
        return [
            # Anti-Captcha Settings
            {
                'setting_key': 'anticaptcha_api_key',
                'setting_value': '',
                'setting_type': 'string',
                'description': 'API key for anti-captcha service',
                'category': 'anticaptcha'
            },
            {
                'setting_key': 'anticaptcha_service',
                'setting_value': '2captcha',
                'setting_type': 'string',
                'description': 'Anti-captcha service provider (2captcha, anticaptcha)',
                'category': 'anticaptcha'
            },
            {
                'setting_key': 'captcha_timeout_seconds',
                'setting_value': '120',
                'setting_type': 'int',
                'description': 'Timeout for captcha solving in seconds',
                'category': 'anticaptcha'
            },

            # Browser Settings
            {
                'setting_key': 'default_browser',
                'setting_value': 'chrome',
                'setting_type': 'string',
                'description': 'Default browser to use (chrome, firefox)',
                'category': 'browser'
            },
            {
                'setting_key': 'headless_mode',
                'setting_value': 'false',
                'setting_type': 'bool',
                'description': 'Run browser in headless mode',
                'category': 'browser'
            },
            {
                'setting_key': 'browser_timeout_seconds',
                'setting_value': '30',
                'setting_type': 'int',
                'description': 'Browser page load timeout in seconds',
                'category': 'browser'
            },
            {
                'setting_key': 'max_browser_instances',
                'setting_value': '5',
                'setting_type': 'int',
                'description': 'Maximum concurrent browser instances',
                'category': 'browser'
            },

            # Warmup Settings
            {
                'setting_key': 'warmup_sites_list',
                'setting_value': '["https://google.com", "https://youtube.com", "https://facebook.com", "https://vk.com", "https://mail.ru", "https://yandex.ru", "https://wikipedia.org"]',
                'setting_type': 'json',
                'description': 'List of sites for profile warmup',
                'category': 'warmup'
            },
            {
                'setting_key': 'warmup_duration_minutes',
                'setting_value': '30',
                'setting_type': 'int',
                'description': 'Default warmup duration in minutes',
                'category': 'warmup'
            },
            {
                'setting_key': 'warmup_min_page_time',
                'setting_value': '30',
                'setting_type': 'int',
                'description': 'Minimum time to spend on each page during warmup (seconds)',
                'category': 'warmup'
            },
            {
                'setting_key': 'warmup_max_page_time',
                'setting_value': '300',
                'setting_type': 'int',
                'description': 'Maximum time to spend on each page during warmup (seconds)',
                'category': 'warmup'
            },

            # Yandex Maps Settings
            {
                'setting_key': 'yandex_visit_min_time',
                'setting_value': '120',
                'setting_type': 'int',
                'description': 'Minimum time to spend on Yandex Maps profile (seconds)',
                'category': 'yandex'
            },
            {
                'setting_key': 'yandex_visit_max_time',
                'setting_value': '600',
                'setting_type': 'int',
                'description': 'Maximum time to spend on Yandex Maps profile (seconds)',
                'category': 'yandex'
            },
            {
                'setting_key': 'yandex_actions_enabled',
                'setting_value': '["scroll", "click_photos", "read_reviews", "click_contacts"]',
                'setting_type': 'json',
                'description': 'Enabled actions on Yandex Maps profiles',
                'category': 'yandex'
            },

            # Task Management
            {
                'setting_key': 'max_concurrent_tasks',
                'setting_value': '10',
                'setting_type': 'int',
                'description': 'Maximum concurrent tasks',
                'category': 'tasks'
            },
            {
                'setting_key': 'task_retry_delay_minutes',
                'setting_value': '5',
                'setting_type': 'int',
                'description': 'Delay between task retries in minutes',
                'category': 'tasks'
            },
            {
                'setting_key': 'task_max_retries',
                'setting_value': '3',
                'setting_type': 'int',
                'description': 'Maximum number of task retries',
                'category': 'tasks'
            },

            # Proxy Settings
            {
                'setting_key': 'proxy_rotation_enabled',
                'setting_value': 'true',
                'setting_type': 'bool',
                'description': 'Enable automatic proxy rotation',
                'category': 'proxy'
            },
            {
                'setting_key': 'proxy_check_interval_minutes',
                'setting_value': '10',
                'setting_type': 'int',
                'description': 'Interval for proxy health checks in minutes',
                'category': 'proxy'
            },
            {
                'setting_key': 'proxy_timeout_seconds',
                'setting_value': '10',
                'setting_type': 'int',
                'description': 'Proxy connection timeout in seconds',
                'category': 'proxy'
            },

            # Rate Limiting
            {
                'setting_key': 'min_request_delay_seconds',
                'setting_value': '5',
                'setting_type': 'int',
                'description': 'Minimum delay between requests in seconds',
                'category': 'rate_limit'
            },
            {
                'setting_key': 'max_request_delay_seconds',
                'setting_value': '30',
                'setting_type': 'int',
                'description': 'Maximum delay between requests in seconds',
                'category': 'rate_limit'
            },
            {
                'setting_key': 'daily_visit_limit_per_ip',
                'setting_value': '100',
                'setting_type': 'int',
                'description': 'Maximum visits per day per IP address',
                'category': 'rate_limit'
            },

            # Security & Privacy
            {
                'setting_key': 'clear_cookies_after_session',
                'setting_value': 'false',
                'setting_type': 'bool',
                'description': 'Clear cookies after each session',
                'category': 'security'
            },
            {
                'setting_key': 'clear_cache_after_session',
                'setting_value': 'false',
                'setting_type': 'bool',
                'description': 'Clear browser cache after each session',
                'category': 'security'
            },
            {
                'setting_key': 'user_agent_rotation',
                'setting_value': 'true',
                'setting_type': 'bool',
                'description': 'Enable user agent rotation',
                'category': 'security'
            },

            # Logging & Monitoring
            {
                'setting_key': 'log_level',
                'setting_value': 'INFO',
                'setting_type': 'string',
                'description': 'Logging level (DEBUG, INFO, WARNING, ERROR)',
                'category': 'logging'
            },
            {
                'setting_key': 'save_screenshots',
                'setting_value': 'true',
                'setting_type': 'bool',
                'description': 'Save screenshots during execution',
                'category': 'logging'
            },
            {
                'setting_key': 'max_log_file_size_mb',
                'setting_value': '100',
                'setting_type': 'int',
                'description': 'Maximum log file size in MB',
                'category': 'logging'
            }
        ]