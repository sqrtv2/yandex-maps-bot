"""
Configuration settings for Yandex Maps Profile Visitor system.
"""
import os
from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    # Application
    app_name: str = Field(default="Yandex Maps Profile Visitor", description="Application name")
    app_version: str = Field(default="1.0.0", description="Application version")
    debug: bool = Field(default=True, description="Debug mode")

    # API Configuration
    api_host: str = Field(default="localhost", description="API host")
    api_port: int = Field(default=8000, description="API port")
    api_reload: bool = Field(default=True, description="Auto-reload API server")

    # Database Configuration
    database_url: str = Field(
        default="sqlite:///./yandex_maps_bot.db",
        description="Database URL"
    )
    database_echo: bool = Field(default=False, description="Echo SQL queries")

    # Redis Configuration (for Celery)
    redis_host: str = Field(default="localhost", description="Redis host")
    redis_port: int = Field(default=6379, description="Redis port")
    redis_db: int = Field(default=0, description="Redis database number")
    redis_password: Optional[str] = Field(default=None, description="Redis password")

    @property
    def redis_url(self) -> str:
        """Construct Redis URL."""
        auth_part = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth_part}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    # Celery Configuration
    celery_broker_url: Optional[str] = Field(default=None, description="Celery broker URL")
    celery_result_backend: Optional[str] = Field(default=None, description="Celery result backend")
    celery_task_serializer: str = Field(default="json", description="Celery task serializer")
    celery_result_serializer: str = Field(default="json", description="Celery result serializer")
    celery_accept_content: list = Field(default=["json"], description="Celery accepted content types")
    celery_timezone: str = Field(default="UTC", description="Celery timezone")
    celery_enable_utc: bool = Field(default=True, description="Enable UTC for Celery")
    celery_worker_concurrency: int = Field(
        default=12, 
        description="Number of parallel Celery worker processes"
    )

    @property
    def celery_config(self) -> dict:
        """Get Celery configuration dictionary."""
        return {
            'broker_url': self.celery_broker_url or self.redis_url,
            'result_backend': self.celery_result_backend or self.redis_url,
            'task_serializer': self.celery_task_serializer,
            'result_serializer': self.celery_result_serializer,
            'accept_content': self.celery_accept_content,
            'timezone': self.celery_timezone,
            'enable_utc': self.celery_enable_utc,
            'task_routes': {
                'tasks.warmup.*': {'queue': 'warmup'},
                'tasks.yandex_maps.*': {'queue': 'yandex'},
                'tasks.proxy.*': {'queue': 'proxy'},
            },
            'worker_prefetch_multiplier': 1,
            'task_acks_late': True,
        }

    # Security
    secret_key: str = Field(
        default="your-secret-key-change-this-in-production",
        description="Secret key for JWT tokens"
    )
    access_token_expire_minutes: int = Field(default=30, description="JWT token expiration time")

    # Browser Configuration
    browser_binary_path: Optional[str] = Field(default=None, description="Custom browser binary path")
    browser_user_data_dir: str = Field(default="./browser_profiles", description="Browser profiles directory")
    browser_download_dir: str = Field(default="./downloads", description="Browser downloads directory")
    browser_headless: bool = Field(default=False, description="Run browser in headless mode")
    browser_timeout: int = Field(default=30, description="Browser timeout in seconds")
    max_browser_instances: int = Field(default=16, description="Maximum concurrent browser instances")

    # Anti-Captcha Configuration
    anticaptcha_api_key: str = Field(default="", description="Anti-captcha API key")
    anticaptcha_service: str = Field(default="2captcha", description="Anti-captcha service (2captcha, anticaptcha)")
    captcha_timeout: int = Field(default=120, description="Captcha solving timeout in seconds")

    # Proxy Configuration
    proxy_check_url: str = Field(default="http://httpbin.org/ip", description="URL for proxy health checks")
    proxy_timeout: int = Field(default=10, description="Proxy timeout in seconds")
    proxy_max_retries: int = Field(default=3, description="Maximum proxy retries")

    # Rate Limiting
    min_request_delay: int = Field(default=2, description="Minimum delay between requests in seconds")
    max_request_delay: int = Field(default=10, description="Maximum delay between requests in seconds")
    daily_visit_limit: int = Field(default=10000, description="Daily visit limit per IP")

    # Warmup Configuration
    warmup_sites: list = Field(
        default=[
            "https://google.com",
            "https://youtube.com",
            "https://facebook.com",
            "https://vk.com",
            "https://mail.ru",
            "https://yandex.ru",
            "https://wikipedia.org"
        ],
        description="Sites for profile warmup"
    )
    warmup_duration_minutes: int = Field(default=5, description="Warmup duration in minutes")
    warmup_min_page_time: int = Field(default=10, description="Minimum time per page during warmup")
    warmup_max_page_time: int = Field(default=30, description="Maximum time per page during warmup")

    # Yandex Maps Configuration
    yandex_min_visit_time: int = Field(default=30, description="Minimum visit time on Yandex Maps")
    yandex_max_visit_time: int = Field(default=90, description="Maximum visit time on Yandex Maps")
    yandex_actions: list = Field(
        default=["scroll", "click_photos", "read_reviews", "click_contacts"],
        description="Available actions on Yandex Maps"
    )

    # Logging Configuration
    log_level: str = Field(default="INFO", description="Logging level")
    log_format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        description="Log format"
    )
    log_file: str = Field(default="./logs/app.log", description="Log file path")
    max_log_file_size: int = Field(default=100, description="Max log file size in MB")
    log_backup_count: int = Field(default=5, description="Number of log backup files")

    # File Storage
    screenshots_dir: str = Field(default="./screenshots", description="Screenshots directory")
    logs_dir: str = Field(default="./logs", description="Logs directory")
    data_dir: str = Field(default="./data", description="Data directory")

    # Task Configuration
    max_concurrent_tasks: int = Field(default=20, description="Maximum concurrent tasks")
    task_retry_delay: int = Field(default=60, description="Task retry delay in seconds")
    task_max_retries: int = Field(default=2, description="Maximum task retries")
    task_timeout: int = Field(default=300, description="Task timeout in seconds (5 min)")

    # Performance Tuning
    save_screenshots: bool = Field(default=False, description="Save screenshots during visits (disable for speed)")
    fast_mode: bool = Field(default=True, description="Fast mode: reduce sleeps and delays for higher throughput")
    batch_delay_seconds: int = Field(default=30, description="Delay between batch visits (seconds)")

    # Captcha Solver Configuration
    capsola_api_key: str = Field(
        default="9f8a1a9b-4322-4b8a-91ec-49192cdbaeb9",
        description="Capsola Cloud API Key"
    )
    capsola_enabled: bool = Field(default=True, description="Enable Capsola captcha solver")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        env_prefix = "YANDEX_BOT_"
        case_sensitive = False

    def create_directories(self):
        """Create necessary directories."""
        directories = [
            self.browser_user_data_dir,
            self.browser_download_dir,
            self.screenshots_dir,
            self.logs_dir,
            self.data_dir,
            os.path.dirname(self.log_file),
        ]

        for directory in directories:
            if directory and not os.path.exists(directory):
                os.makedirs(directory, exist_ok=True)


# Global settings instance
settings = Settings()

# Create directories on import
settings.create_directories()